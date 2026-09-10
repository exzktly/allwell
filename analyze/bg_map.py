"""The Analyze pane's editor for pairing a blank with the wells it corrects.

Naming several blanks pools them into one plate-wide reference. That is right
when they differ only by noise, and wrong when they differ because of the
treatment — a compound that fluoresces puts real signal into its own block's
blank, and pooling spreads it over the whole plate while the numbers go on
looking corrected. ``--background_well_map`` pairs instead; this is where it is
written.

**It is a plate, not a text field, and that is the point.** The failure the map
exists to prevent is a well quietly landing in the wrong block, and a list of
well names cannot show that: eight rows of ``A01,A02,A03…`` read as correct
whatever they say. A coloured plate shows the partition at a glance, and shows
the wells no blank claims — the ones that will stop the run — before the run is
started.

Lives in its own module because ``analyze/form.py`` is against its line budget
and its own budget note asks for an extraction with the next section. It takes
``_background_well_validation_error`` with it, which belongs beside the map it
now also validates.

**The Background well field says which wells are blanks; this editor only says
which wells each of them corrects.** One question, one control. The first
version of this asked the question twice — the field named the blanks for the
run, and an ``+ Add blank`` button in here named them for the editor — and the
two did not agree in either direction. A blank typed in the field was invisible
here, so the list was empty on open and the manual's own first instruction
("pick a blank in the list") could not be carried out; every drag was a silent
no-op with no colour, no message and no changed label. Meanwhile the button, when
the field named nothing, took a well off the *end of the plate* and made it the
reference: a sample well held out of the results and used to correct the other
ninety-five, with this editor reporting "Every well is claimed — this pairing
will run" and the form raising nothing.

So the list is a projection of the field, seeded when the editor opens and
re-synced whenever the field changes while it is open. Nothing in here can
invent a blank, and there is nothing in here to rename one with.

Three habits borrowed from the editors this is modelled on, all load-bearing:

* **The map lives on the tab and the widgets are a projection of it**, never the
  other way round. Reading membership back out of the widgets is how
  ``fold_change_pairing_view`` describes losing an assignment to a repopulate.
* **A row edit that rebuilds the rows destroys the widget mid-signal.** The
  control whose signal started the edit is one of the widgets the repopulate
  deletes. Guarded with a re-entrancy flag and a zero-delay timer.
* **Every gesture that changes the map is undoable.** A drag takes a well from
  whoever held it, which is the right data rule and an invisible interaction —
  one sweep across a neighbour's block moved four wells and the only trace was a
  count going to zero in a list 70 pixels tall.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QRadioButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from theme import WELL_COLORS_TUPLE
from util.well_token import (
    canonical_well_label,
    format_background_well_map,
    parse_background_well_map,
    split_background_wells,
)
from well_viewer.core.safe import swallow
from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector

#: Every position on a 96-well plate, for the editor's fallback when no input
#: folder has been picked yet. Offering the whole plate is better than offering
#: nothing: a user can write the map before choosing the folder, and the run's
#: own refusal is what holds the map against the plate that turns up.
_ALL_WELLS = [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)]

#: The colour swatch that ties a row in the list to its block on the plate.
#: Sized here rather than at the call site, and small on purpose: it is a key to
#: the plate above it, not a control, so it should not read as something to
#: click. Its own constant rather than a `theme.Metrics` token because no other
#: pane has one — the precedent is `analyze.form_chrome._ROW_LABEL_W`.
_SWATCH_PX = 12
_SWATCH_RADIUS_PX = 3

#: Drawn on a blank well so the reference is not mistaken for one of the wells
#: it corrects. A glyph rather than another colour because the fill already
#: carries the block and the ring already carries "this is the one being edited".
_BG_MAP_BLANK_GLYPH = "◎"

#: One row of the blank list, for sizing the scroll area that holds them. A
#: radio button plus a swatch, with the layout's own 1px margins either side.
_BG_MAP_ROW_PX = 26

#: How many gestures Undo can step back through. Deep enough to recover from a
#: mis-drag and the two or three that followed it before you noticed, shallow
#: enough that the stack is not a second copy of the session's whole history.
_BG_MAP_UNDO_DEPTH = 20


class AnalyzeBackgroundMapMixin:
    """The Background-well pairing control, its editor, and their validation."""

    #: ``{blank: [wells it corrects]}``, canonical and ordered. The single copy;
    #: every widget below is drawn from it.
    _background_map: dict[str, list[str]]
    #: Guards the repopulate-inside-a-signal re-entrancy described above.
    _bg_map_in_change = False
    #: Snapshots of :attr:`_background_map`, most recent last. Pushed before a
    #: gesture rather than after, so Undo returns to the state the user could
    #: see when they started it.
    _bg_map_undo: list[dict[str, list[str]]]
    #: How many rows of the blank list the window opens tall enough to show.
    #: Four because a row split of a 96-well plate makes eight, and a list that
    #: shows two and a half of them is one the user cannot read.
    _BG_MAP_VISIBLE_ROWS = 4

    # ── the row in the Quality Control section ───────────────────────────
    def _build_background_map_row(self, sec: QWidget) -> None:
        self._background_map = {}
        self._bg_map_active = 0
        self._bg_map_window = None
        self._bg_map_undo = []
        row = self._row(sec, "Blank pairing")
        self._bg_map_summary = QLabel("Pooled — every blank corrects every well")
        self._bg_map_summary.setObjectName("Muted")
        self._bg_map_summary.setWordWrap(True)
        row.layout().addWidget(self._bg_map_summary, 1)
        self._bg_map_btn = QPushButton("Pair blanks…", row)
        self._bg_map_btn.setProperty("variant", "secondary")
        self._bg_map_btn.setToolTip(
            "Pair each blank with the wells it corrects, instead of pooling "
            "every Background well into one plate-wide reference. Use this when "
            "the blanks differ because of the treatment — a compound that "
            "fluoresces puts real signal into its own blank, and pooling "
            "spreads it over the whole plate. With a pairing in force every "
            "well the run processes must be named by exactly one blank, or the "
            "run refuses to start.")
        self._bg_map_btn.clicked.connect(self._open_background_map_editor)
        row.layout().addWidget(self._bg_map_btn)
        row.layout().addStretch(1)

    def _sync_background_map_enabled(self) -> None:
        """Enabled on the same condition as the Background well field itself."""
        on = "empty_well" in (self._flatfield_cb.currentText(),
                              self._background_cb.currentText())
        for widget in (self._bg_map_btn, self._bg_map_summary):
            widget.setEnabled(on)

    # ── what the run and the sidecar see ─────────────────────────────────
    def _background_map_value(self) -> str:
        """The written form, or ``""`` for a pooled run.

        Always the explicit well list, never a range: what is stored has to mean
        exactly one thing when it is read back, and a rectangle stops being true
        the moment one well is edited out of the block.
        """
        return format_background_well_map(self._background_map)

    def _set_background_map(self, value) -> None:
        """Restore a map from a sidecar or a queued job; ``""`` means pooled."""
        try:
            self._background_map = parse_background_well_map(value)
        except ValueError:
            # A sidecar can be hand-edited, and a map the parser refuses is one
            # the run would refuse too. Dropping it leaves the form pooled and
            # says so, which is the honest reading of "this cannot be applied".
            self._background_map = {}
        self._bg_map_active = 0
        self._bg_map_undo = []
        # A restored map may name blanks the Background well field does not, and
        # the field is what this editor projects — so without this the next
        # keystroke in the field would quietly drop those blanks and their
        # blocks. Unioned into the field instead, which is also what the run
        # does with the two flags (``util.well_token.blank_wells_for_run``), so
        # the form now says out loud what the pipeline was going to conclude.
        self._bg_map_adopt_blanks_into_field()
        self._refresh_background_map_summary()
        # And the editor, if it is open. Refreshing only the summary left the
        # window showing the *previous* map: rows naming blanks the new map does
        # not have, a stale coverage line, stale colours — and a drag then
        # painted into whichever blank happened to be first in the new map.
        self._refresh_background_map_editor()

    def _bg_map_adopt_blanks_into_field(self) -> None:
        """Add this map's blanks to the Background well field, keeping its order."""
        edit = getattr(self, "_background_well_edit", None)
        if edit is None or not self._background_map:
            return
        named = self._bg_map_field_blanks()
        missing = [b for b in self._background_map if b not in named]
        if not missing:
            return
        edit.setText(", ".join(named + missing))

    def _bg_map_say(self, message: str = "") -> None:
        """Put a transient note on the second line, or clear it.

        Separate from the coverage line so a message can be cleared without
        taking the unclaimed-well count with it, and so a refusal cannot outlive
        its cause: the split's "name a blank first" used to sit there until the
        window was closed and reopened, long after the field had been filled in.
        """
        label = getattr(self, "_bg_map_status", None)
        if label is not None:
            label.setText(message)

    # ── the blanks come from the Background well field ───────────────────
    def _bg_map_sync_blanks_from_field(self) -> None:
        """Make the blank list say exactly what the Background well field says.

        The field is the one place a well is declared a blank — it is what the
        run reads, through ``util.well_token.blank_wells_for_run`` — so the
        editor projects it rather than keeping a second list beside it. A well
        newly named there arrives here with no wells yet; a well no longer named
        there stops being a blank, and the wells it held become unclaimed, which
        the coverage line then says out loud.

        Called when the editor opens and on every keystroke in the field while it
        is open, so "pick a blank in the list" is true the moment the window
        appears — the state a first-time user actually lands in.
        """
        named: list[str] = []
        for candidate in split_background_wells(
                self._background_well_edit.text()):
            well = canonical_well_label(candidate)
            if well and well not in named:
                named.append(well)
        if named == list(self._background_map) and all(
                w in named for w in self._background_map):
            return
        self._background_map = {w: self._background_map.get(w, []) for w in named}
        # A well can only be corrected by a blank that still exists, and a blank
        # is never one of its own samples.
        for blank, wells in self._background_map.items():
            self._background_map[blank] = [w for w in wells if w != blank]
        self._bg_map_active = min(self._bg_map_active, max(len(named) - 1, 0))

    def _bg_map_on_field_changed(self) -> None:
        """Re-project the field onto the list, but only while the window is up."""
        if getattr(self, "_bg_map_window", None) is None:
            return
        self._bg_map_sync_blanks_from_field()
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    # ── undo ─────────────────────────────────────────────────────────────
    def _bg_map_push_undo(self) -> None:
        """Snapshot the map before changing it.

        Deep enough to be independent — the well lists are mutated in place by
        the paint handlers, so a shallow copy would alias the very lists it is
        meant to preserve.
        """
        self._bg_map_undo.append(
            {blank: list(wells) for blank, wells in self._background_map.items()})
        del self._bg_map_undo[:-_BG_MAP_UNDO_DEPTH]

    def _bg_map_undo_last(self) -> None:
        """Step back one gesture, or say there is nothing to step back to."""
        if not self._bg_map_undo:
            self._bg_map_say("Nothing to undo.")
            return
        self._background_map = self._bg_map_undo.pop()
        self._bg_map_active = min(
            self._bg_map_active, max(len(self._background_map) - 1, 0))
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()
        self._bg_map_say("Undone." if self._bg_map_undo else
                         "Undone — back to where this editor opened.")

    def _refresh_background_map_summary(self) -> None:
        """What the *run* will do, which is not always what the editor holds.

        Asked of :meth:`_background_map_value` rather than of the tab's own dict
        because a blank with no wells is dropped on the way out — so a map of
        nothing but empty blanks is a non-empty dict here and an empty string to
        the run. Reading the dict said "H12 → 0 well(s)", i.e. that a pairing is
        in force, for a run that pools.
        """
        if not self._background_map_value():
            self._bg_map_summary.setText(
                "Pooled — every blank corrects every well"
                if not self._background_map else
                "Pooled — no blank has any wells yet")
            return
        self._bg_map_summary.setText("; ".join(
            f"{blank} → {len(wells)} well(s)"
            for blank, wells in self._background_map.items() if wells))

    # ── the wells this plate actually has ────────────────────────────────
    def _background_map_plate_wells(self) -> list[str]:
        """The input folder's own wells, or the whole plate when none is known.

        Read from the *resolved* input directory rather than the text field: on
        the common ``<dataset>/in`` layout the field names the parent, whose own
        listing holds no wells at all, and the editor would offer an empty plate.
        """
        from services.input_resolution_service import iter_all_well_tokens

        raw = getattr(self, "_preflight_input", "") or self._input_edit.text().strip()
        if not raw:
            return list(_ALL_WELLS)
        wells: list[str] = []
        with swallow("listing the plate's wells for the pairing editor"):
            wells = iter_all_well_tokens(
                Path(raw), filename_schema=self._build_schema_arg(),
                filename_sep=self._filename_sep())
        return wells or list(_ALL_WELLS)

    # ── the editor window ────────────────────────────────────────────────
    def _open_background_map_editor(self) -> None:
        """Show the editor, building it on first use.

        Built once and hidden rather than destroyed, so a user who closes it
        mid-edit and reopens it finds the same window. Non-modal, because the
        map is judged against the plate and against the Background well field
        beside it, and a modal dialog would hide the second.
        """
        if self._bg_map_window is None:
            self._bg_map_window = self._build_background_map_window()
            # While the window exists, the field it projects keeps it current.
            # Connected here rather than where the field is built because the
            # field belongs to the form and this is the only thing that watches
            # it; connecting once, on first open, also means a form that never
            # opens the editor pays nothing.
            self._background_well_edit.textChanged.connect(
                lambda _text: self._bg_map_on_field_changed())
        self._bg_map_sync_blanks_from_field()
        self._bg_map_say()
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()
        self._bg_map_window.show()
        self._bg_map_window.raise_()

    def _build_background_map_window(self) -> QDialog:
        win = QDialog(self)
        win.setWindowTitle("Pair blanks with the wells they correct")
        win.setModal(False)
        outer = QVBoxLayout(win)

        self._bg_map_hint = QLabel("")
        self._bg_map_hint.setWordWrap(True)
        outer.addWidget(self._bg_map_hint)

        self._bg_map_plate = WellPlateSelector()
        apply_plate_geometry(self._bg_map_plate, role="editor")
        self._bg_map_plate.setSelectionModel("paint")
        self._bg_map_plate.setColoredWellsLit(True)
        # Headers are inert here for the reason Batch Export's editor gives:
        # the row/column actions below replace the whole map, so putting one on
        # a stray click at the plate's edge would destroy the user's work.
        self._bg_map_plate.setRowColumnSelectable(False)
        self._bg_map_plate.wellPaintPressed.connect(self._bg_map_press)
        self._bg_map_plate.wellPaintDragged.connect(self._bg_map_paint)
        self._bg_map_plate.paintDragFinished.connect(self._bg_map_release)
        outer.addWidget(self._bg_map_plate, 0, PLATE_ALIGNMENT)

        # Two labels, not one. The coverage line has to state the number of
        # unclaimed wells at all times — it is the run's refusal shown early —
        # and a split that cannot fill the plate used to overwrite it with its
        # own message, destroying the count at the moment it was largest. So
        # anything transient goes to a second line that can be cleared without
        # taking the count with it.
        self._bg_map_uncovered = QLabel("")
        self._bg_map_uncovered.setWordWrap(True)
        outer.addWidget(self._bg_map_uncovered)

        self._bg_map_status = QLabel("")
        self._bg_map_status.setObjectName("Muted")
        self._bg_map_status.setWordWrap(True)
        outer.addWidget(self._bg_map_status)

        scroll = QScrollArea(win)
        scroll.setWidgetResizable(True)
        self._bg_map_rows_host = QWidget()
        self._bg_map_rows_layout = QVBoxLayout(self._bg_map_rows_host)
        self._bg_map_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._bg_map_rows_layout.addStretch(1)
        scroll.setWidget(self._bg_map_rows_host)
        # Tall enough for four rows. The list is what the manual tells you to
        # pick from, and it was getting 70px of a 404px window — two and a half
        # of the eight rows a row split produces, the third sliced in half.
        scroll.setMinimumHeight(_BG_MAP_ROW_PX * self._BG_MAP_VISIBLE_ROWS)
        outer.addWidget(scroll, 1)

        footer = QHBoxLayout()
        # No "add a blank" control: a blank is declared in the Background well
        # field and nowhere else. The button that used to be here invented one
        # off the end of the plate when the field named none, which turns a
        # sample well into the reference for the rest of the plate.
        self._bg_map_undo_btn = QPushButton("Undo", win)
        self._bg_map_undo_btn.setProperty("variant", "secondary")
        self._bg_map_undo_btn.setToolTip(
            "Step back one drag, split or clear (Ctrl+Z).")
        self._bg_map_undo_btn.clicked.connect(self._bg_map_undo_last)
        footer.addWidget(self._bg_map_undo_btn)
        undo_key = QShortcut(QKeySequence.StandardKey.Undo, win)
        undo_key.activated.connect(self._bg_map_undo_last)

        split = QToolButton(win)
        split.setText("Split plate…")
        split.setPopupMode(QToolButton.InstantPopup)
        split.setStyleSheet(
            "QToolButton::menu-indicator { image: none; width: 0px; }")
        # Each entry states the blocks it makes against the blanks you have, so
        # a split that cannot fill the plate is visible before it is chosen
        # rather than as seventy unclaimed wells after it. Rebuilt on open,
        # because both numbers change.
        self._bg_map_split_menu = QMenu(split)
        self._bg_map_split_menu.aboutToShow.connect(self._bg_map_rebuild_split_menu)
        self._bg_map_rebuild_split_menu()
        split.setMenu(self._bg_map_split_menu)
        footer.addWidget(split)

        clear = QPushButton("Clear pairing", win)
        clear.setProperty("variant", "secondary")
        clear.setToolTip("Drop the map and pool every blank into one reference.")
        clear.clicked.connect(self._bg_map_clear)
        footer.addWidget(clear)

        footer.addStretch(1)
        close = QPushButton("Close", win)
        close.clicked.connect(win.hide)
        footer.addWidget(close)
        outer.addLayout(footer)
        return win

    def _bg_map_rebuild_split_menu(self) -> None:
        """Label each split with the blocks it needs and the blanks available."""
        menu = getattr(self, "_bg_map_split_menu", None)
        if menu is None:
            return
        menu.clear()
        have = len(self._bg_map_field_blanks())
        for how, label in (("row", "One blank per row (A, B, C…)"),
                           ("col", "One blank per column (01, 02…)"),
                           ("quadrant", "One blank per quadrant"),
                           ("even", "Even split, one block per blank")):
            need = len(self._bg_map_blocks(how))
            fits = have >= need
            text = (f"{label} — {need} block(s), you have {have} blank(s)"
                    if not fits else f"{label} — {need} block(s)")
            action = menu.addAction(
                text, lambda h=how: self._bg_map_split(h))
            # Left enabled rather than greyed out: an under-supplied split is a
            # usable starting point that the user then paints on top of, and a
            # disabled item cannot say why it is disabled.
            action.setEnabled(have > 0)

    @staticmethod
    def _bg_map_color(index: int) -> str:
        """A distinct colour per blank, by its position in the list.

        By list position and not by the wells' plate rank: two blanks whose
        blocks span the same columns would otherwise take the same hue, since
        well ranking is column-based — and two blocks drawn in one colour is
        exactly the partition this editor exists to make visible.
        """
        return WELL_COLORS_TUPLE[index % len(WELL_COLORS_TUPLE)]

    def _bg_map_blanks(self) -> list[str]:
        return list(self._background_map)

    # ── painting ─────────────────────────────────────────────────────────
    def _bg_map_press(self, well: str) -> None:
        """Latch the gesture's direction from the well it started on.

        One drag adds or removes throughout, decided by its first well, rather
        than each well toggling under the cursor — dragging back over your own
        block should undo it, not stipple it.
        """
        blanks = self._bg_map_blanks()
        if not blanks:
            # Never silently. A drag that does nothing and says nothing is what
            # the first version of this did on every cold open, and it is
            # indistinguishable from a dialog that is simply broken.
            self._bg_map_say(
                "No blanks to paint onto. Name your blank well(s) in the "
                "Background well field beside this button — those are the "
                "wells that will appear in the list below.")
            return
        self._bg_map_push_undo()
        blank = blanks[min(self._bg_map_active, len(blanks) - 1)]
        self._bg_map_adding = well not in self._background_map.get(blank, [])
        self._bg_map_paint(well)

    def _bg_map_paint(self, well: str) -> None:
        blanks = self._bg_map_blanks()
        if not blanks:
            return
        blank = blanks[min(self._bg_map_active, len(blanks) - 1)]
        if well == blank:
            return          # a blank is the reference, never one of its samples
        if self._bg_map_adding:
            # One well, one blank: taking it from whoever held it is what keeps
            # the map unambiguous, and the parser refuses a map where two blanks
            # claim the same well anyway.
            for other, wells in self._background_map.items():
                if other != blank and well in wells:
                    wells.remove(well)
            if well not in self._background_map[blank]:
                self._background_map[blank] = sorted(
                    self._background_map[blank] + [well])
        elif well in self._background_map.get(blank, []):
            self._background_map[blank].remove(well)
        self._bg_map_repaint_plate()

    def _bg_map_release(self) -> None:
        """Finish the gesture, and name anything it took from another blank.

        The steal is the right data rule — one well, one blank — and an
        invisible interaction: one sweep back across a neighbour's block moved
        four wells, and the only trace was a count going to zero in a list too
        short to show the row it happened on.
        """
        before = self._bg_map_undo[-1] if self._bg_map_undo else {}
        active = self._bg_map_blanks()
        active_blank = (active[min(self._bg_map_active, len(active) - 1)]
                        if active else "")
        robbed = {
            blank: len(set(wells) - set(self._background_map.get(blank, [])))
            for blank, wells in before.items()
            if blank != active_blank and blank in self._background_map
        }
        taken = {b: n for b, n in robbed.items() if n}
        if taken:
            self._bg_map_say("Took " + ", ".join(
                f"{n} well(s) from {b}" for b, n in taken.items())
                + f" and gave them to {active_blank}. Undo puts them back.")
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    # ── the list of blanks ───────────────────────────────────────────────
    def _bg_map_clear_blank(self, blank: str) -> None:
        """Take every well away from *blank*, leaving it a blank with no block.

        Not "remove this blank": the Background well field decides that, and a
        row that could delete a blank the field still names would be the second
        answer to that question this editor used to carry.
        """
        if blank not in self._background_map:
            return
        self._bg_map_push_undo()
        self._background_map[blank] = []
        self._bg_map_say(f"{blank} now corrects nothing.")
        self._bg_map_defer_refresh()

    def _bg_map_defer_refresh(self) -> None:
        """Repopulate after the signal that asked for it has finished.

        The combo whose ``currentIndexChanged`` started this edit is one of the
        widgets the repopulate deletes, so rebuilding the rows inline destroys
        the object mid-emission. The flag stops the rebuild's own
        ``setCurrentText`` calls from re-entering here.
        """
        if self._bg_map_in_change:
            return
        self._bg_map_in_change = True

        def _done() -> None:
            self._bg_map_in_change = False
            self._refresh_background_map_editor()
            self._refresh_background_map_summary()

        QTimer.singleShot(0, _done)

    def _bg_map_clear(self) -> None:
        """Drop every block, leaving the field's blanks pooled.

        Undoable rather than confirmed: a dialog asking "are you sure" before
        an action that can be taken back is a click spent on nothing, and this
        editor has no destructive action that cannot be.
        """
        self._bg_map_push_undo()
        self._bg_map_sync_blanks_from_field()
        for blank in self._background_map:
            self._background_map[blank] = []
        self._bg_map_active = 0
        self._bg_map_say("Pairing cleared — every blank pools again. Undo "
                         "brings the blocks back.")
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    def _bg_map_blocks(self, how: str) -> list[list[str]]:
        """The plate cut into blocks the named way, absent wells excluded.

        Taken from the plate's own wells, so a half-populated plate gets
        half-blocks rather than blocks of wells the folder does not have.
        ``"even"`` divides the plate into exactly as many contiguous blocks as
        there are blanks, which is the split the common case actually wants: two
        blanks and "one blank per row" means eight blocks, six of them with no
        blank and seventy wells left unclaimed.
        """
        wells = self._background_map_plate_wells()
        if how == "row":
            keys = sorted({w[0] for w in wells})
            return [[w for w in wells if w[0] == k] for k in keys]
        if how == "col":
            keys = sorted({w[1:] for w in wells})
            return [[w for w in wells if w[1:] == k] for k in keys]
        if how == "even":
            n = max(len(self._bg_map_field_blanks()), 1)
            columns = sorted({w[1:] for w in wells})
            size = max(1, -(-len(columns) // n))       # ceil, so n blocks cover
            return [[w for w in wells if w[1:] in columns[i:i + size]]
                    for i in range(0, len(columns), size)]

        def quadrant(w: str) -> tuple[bool, bool]:
            return (w[0] >= "E", int(w[1:]) > 6)
        keys = sorted({quadrant(w) for w in wells})
        return [[w for w in wells if quadrant(w) == k] for k in keys]

    def _bg_map_field_blanks(self) -> list[str]:
        """The canonical blanks the Background well field names, in its order."""
        seen: list[str] = []
        for candidate in split_background_wells(
                self._background_well_edit.text()):
            well = canonical_well_label(candidate)
            if well and well not in seen:
                seen.append(well)
        return seen

    def _bg_map_split(self, how: str) -> None:
        """Replace the map with one blank per row / column / quadrant.

        Each blank takes the block it **sits in** where it sits in one, and the
        blocks nobody sits in are handed out in order to the blanks left over.
        Assigning strictly by list position instead put the blank in row H in
        charge of row A, which is the opposite of what a user picking "one blank
        per row" is asking for and is invisible until the numbers are wrong.
        """
        blocks = self._bg_map_blocks(how)
        # Blanks come only from the Background well field, never from the
        # plate's remaining wells. Inventing one turns a sample well into a
        # reference — silently, and against the whole point of pairing — and
        # the first draft of this did exactly that: it took H12, H11, H10… off
        # the end of the plate, so row H's blank sat inside row H's own block
        # and was corrected by itself.
        blanks = self._bg_map_field_blanks()
        if not blanks:
            self._bg_map_say(
                "Name the blank well(s) in Background well first — a split "
                "assigns the blanks you have, and will not turn a sample well "
                "into a reference.")
            return

        self._bg_map_push_undo()
        held_out = set(blanks)
        # A blank that lives inside a block gets that block; the rest are dealt
        # out in order to whatever is left.
        home = {}
        spare_blocks = []
        for block in blocks:
            inside = [b for b in blanks if b in block and b not in home.values()]
            if inside:
                home[id(block)] = inside[0]
            else:
                spare_blocks.append(block)
        leftover = [b for b in blanks if b not in home.values()]
        for block in spare_blocks:
            if not leftover:
                break
            home[id(block)] = leftover.pop(0)

        mapping: dict[str, list[str]] = {}
        for blank in blanks:                      # keep the field's own order
            for block in blocks:
                if home.get(id(block)) != blank:
                    continue
                claimed = sorted(w for w in block if w not in held_out)
                if claimed:
                    mapping.setdefault(blank, []).extend(claimed)
        self._background_map = {b: sorted(mapping.get(b, [])) for b in blanks}
        self._bg_map_active = 0
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()
        if len(blanks) < len(blocks):
            self._bg_map_say(
                f"{len(blanks)} blank(s) for {len(blocks)} block(s), so "
                f"{len(blocks) - len(blanks)} block(s) went to nobody. Name "
                f"more blanks in Background well, choose the even split, or "
                f"paint the rest onto a blank below.")
        else:
            self._bg_map_say(
                f"Split into {len(blocks)} block(s). Undo puts back what was "
                f"here before.")

    # ── drawing the map back onto the widgets ────────────────────────────
    def _bg_map_repaint_plate(self) -> None:
        """Colour by blank, ring the active blank's block, disable absent wells.

        Absent wells are disabled rather than tinted by hand: the plate already
        draws an absent well as an empty socket, and a disabled well ignores the
        paint gesture for free — so a drag across the plate cannot assign a well
        the input folder does not have.
        """
        plate = getattr(self, "_bg_map_plate", None)
        if plate is None:
            return
        available = self._background_map_plate_wells()
        colors: dict[str, str | None] = {w: None for w in _ALL_WELLS}
        blanks = self._bg_map_blanks()
        for index, blank in enumerate(blanks):
            shade = self._bg_map_color(index)
            colors[blank] = shade
            for well in self._background_map[blank]:
                colors[well] = shade
        plate.setEnabledWells(sorted(available))
        plate.setWellColors(colors)
        # A blank and the wells it corrects shared one fill, so nothing on the
        # plate said which of them was the reference — on a plate whose whole
        # job is to show that partition. The glyph is the one channel free:
        # the fill is the block, the ring is the block being edited.
        plate.clearWellMarkers()
        for blank in blanks:
            plate.setWellMarker(blank, _BG_MAP_BLANK_GLYPH)
        # Which blank you are editing is a different question from which blank a
        # well belongs to, so it gets the ring rather than overloading the fill.
        active = blanks[self._bg_map_active] if blanks else None
        ringed: set[str] = set()
        if active:
            ringed = set(self._background_map.get(active, [])) | {active}
        plate.setWellRings(sorted(ringed))

    def _refresh_background_map_editor(self) -> None:
        if getattr(self, "_bg_map_window", None) is None:
            return
        self._bg_map_repaint_plate()
        self._bg_map_rebuild_rows()
        self._bg_map_refresh_uncovered()
        self._bg_map_refresh_hint()
        undo = getattr(self, "_bg_map_undo_btn", None)
        if undo is not None:
            undo.setEnabled(bool(self._bg_map_undo))

    def _bg_map_refresh_hint(self) -> None:
        """Say what to do *next*, which is not the same in both states.

        A fixed line reading "pick a blank below" is a lie on a plate with no
        blanks, and that is the state a first-time user opens this in: it told
        them to do something the window offered no way to do, which is the whole
        of the report this editor was rewritten for.
        """
        hint = getattr(self, "_bg_map_hint", None)
        if hint is None:
            return
        if not self._background_map:
            hint.setText(
                "No blanks yet. Type the empty well(s) into <b>Background "
                "well</b>, beside the button that opened this — they appear "
                "here as you type, and you drag on the plate to give each one "
                "the wells it corrects.")
            return
        hint.setText(
            "Pick a blank below, then drag across the plate to give it the "
            "wells it corrects. Every well must be claimed by exactly one "
            "blank, or the run will refuse to start.")

    def _bg_map_refresh_uncovered(self) -> None:
        """Say how many wells nobody claims — the run's own refusal, in advance.

        Shown whether or not it is zero, and in the affirmative when it is:
        "0 uncovered" and a missing line look identical, and only one of them
        means the map is finished.
        """
        from pipeline.empty_well import BlankGroups

        value = self._background_map_value()
        if not value:
            self._bg_map_uncovered.setText(
                "Pooled — no pairing in force. Every blank corrects every well."
                if not self._background_map else
                "No blank claims any wells yet, so this run would pool. Drag "
                "across the plate to give a blank its block.")
            return

        groups = BlankGroups("", value, corrections=("empty_well",))
        missing = groups.uncovered(self._background_map_plate_wells())
        if not missing:
            self._bg_map_uncovered.setText(
                "Every well is claimed — this pairing will run.")
            return
        shown = ", ".join(missing[:12]) + ("…" if len(missing) > 12 else "")
        self._bg_map_uncovered.setText(
            f"{len(missing)} well(s) claimed by no blank, so the run will "
            f"refuse to start: {shown}")

    def _bg_map_rebuild_rows(self) -> None:
        """One row per blank: its colour, its well, its count, and a clear."""
        layout = self._bg_map_rows_layout
        while layout.count() > 1:                 # keep the trailing stretch
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparented *before* the deferred delete. `deleteLater` alone
                # leaves the row a child of the host until the event loop turns,
                # and this rebuilds on every paint tick — so the host carries a
                # crowd of pending-delete rows for the whole of a drag, and
                # anything reading the list through `findChildren` (a test, or
                # the button group) sees the dead ones alongside the live.
                widget.setParent(None)
                widget.deleteLater()

        # A group, so picking one row un-picks the last without this having to
        # track it, and so arrow keys move between blanks.
        self._bg_map_row_group = QButtonGroup(self._bg_map_rows_host)
        self._bg_map_row_group.setExclusive(True)
        for index, blank in enumerate(self._bg_map_blanks()):
            layout.insertWidget(index, self._bg_map_build_row(index, blank))

    def _bg_map_build_row(self, index: int, blank: str) -> QWidget:
        """The row is the picker.

        It used to hold three things that could be clicked — an 18-pixel ``●``
        that was the picker and said so only in a tooltip, a combo of all 96
        wells that *looked* like the picker and was actually a rename, and a
        ``✕``. Picking a well from the combo replaced the user's blank with a
        sample well, and the run then refused with a message about the input
        folder, which is not where the problem was. There is nothing to rename
        with now — the Background well field owns which wells are blanks — and
        the one thing left to click is a radio spanning the row, which is what
        "pick a blank in the list" has meant everywhere else since dialogs
        existed.
        """
        row = QWidget(self._bg_map_rows_host)
        line = QHBoxLayout(row)
        line.setContentsMargins(2, 1, 2, 1)

        swatch = QLabel(row)
        swatch.setFixedSize(_SWATCH_PX, _SWATCH_PX)
        swatch.setStyleSheet(f"background:{self._bg_map_color(index)};"
                             f"border-radius:{_SWATCH_RADIUS_PX}px;")
        line.addWidget(swatch)

        count = len(self._background_map[blank])
        pick = QRadioButton(f"{blank}   {count} well(s)", row)
        pick.setChecked(index == self._bg_map_active)
        pick.setToolTip(f"Drag on the plate to give {blank} the wells it "
                        f"corrects.")
        pick.clicked.connect(lambda _=False, i=index: self._bg_map_set_active(i))
        self._bg_map_row_group.addButton(pick, index)
        line.addWidget(pick, 1)

        clear = QToolButton(row)
        clear.setText("✕")
        clear.setToolTip(f"Take every well away from {blank}. {blank} stays a "
                         f"blank — remove it from the Background well field to "
                         f"stop it being one.")
        clear.clicked.connect(
            lambda _=False, b=blank: self._bg_map_clear_blank(b))
        line.addWidget(clear)
        return row

    def _bg_map_set_active(self, index: int) -> None:
        self._bg_map_active = index
        self._bg_map_say()
        self._refresh_background_map_editor()

    # ── validation, moved here with the field it now shares ──────────────
    def _background_well_validation_error(self) -> str | None:
        """Block the run when an ``empty_well`` request cannot be honoured.

        Three questions, in the order a user would fix them: is a blank named at
        all, is every named blank actually in the input, and — when a pairing is
        in force — does it claim every well the run will process. The third is
        the pipeline's own refusal asked early, so it is seen while the form is
        still open rather than after the run starts.

        Lives here rather than in ``analyze/form.py`` because it now validates
        the map as well as the field, and because that file is at its budget.
        """
        uses = []
        if self._flatfield_cb.currentText() == "empty_well":
            uses.append("flat-field correction")
        if self._background_cb.currentText() == "empty_well":
            uses.append("background subtraction")
        if not uses:
            return None
        which = " and ".join(uses)

        from services.input_resolution_service import well_present_in_input
        from util.well_token import blank_wells_for_run

        mapping = self._background_map_value()
        wells = blank_wells_for_run(self._background_well_edit.text().strip(),
                                    mapping)
        if not wells:
            return (f"Empty-well {which} requires a Background well.\n\n"
                    "Enter one empty/background well (e.g. H12) — or several "
                    "comma-separated (e.g. H12, G12) — so its measured "
                    "background can be sampled.")

        input_dir = Path(self._input_edit.text().strip())
        sep = self._schema_picker.separator()
        schema = [f.strip().lower()
                  for f in self._build_schema_arg().split(":") if f.strip()]
        missing = [w for w in wells
                   if not well_present_in_input(input_dir, w, schema=schema,
                                                sep=sep)]
        if missing:
            label = ", ".join(f"'{w}'" for w in missing)
            return (f"Background well {label} was not found in the input folder."
                    f"\n\nEmpty-well {which} reads its reference images from the "
                    f"named well(s) — confirm they exist in:\n{input_dir}")
        return self._background_map_coverage_error(
            mapping, self._background_well_edit.text().strip())

    def _background_map_coverage_error(self, mapping: str,
                                       named: str = "") -> str | None:
        """The pairing refusals, asked of the form rather than the run.

        Both of them, from the same object the pipeline builds: a blank named in
        *named* that the map does not use is refused by the constructor, and a
        well no blank claims by :meth:`BlankGroups.uncovered`. Asking here is
        what turns either into a message before a run starts instead of a
        ``SystemExit`` after one does.
        """
        if not mapping:
            return None
        from pipeline.empty_well import BlankGroups

        try:
            groups = BlankGroups(named, mapping, corrections=("empty_well",))
        except ValueError as exc:
            return str(exc)
        uncovered = groups.uncovered(self._background_map_plate_wells())
        if not uncovered:
            return None
        return (f"{len(uncovered)} well(s) are not paired with any blank:\n"
                f"{', '.join(uncovered)}\n\n"
                "Every well the run processes needs a blank, because a well "
                "corrected by the wrong block's reference produces numbers that "
                "look exactly like correct ones. Open Pair blanks… to claim "
                "them, or clear the pairing to pool every blank instead.")
