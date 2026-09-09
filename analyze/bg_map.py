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

Two habits borrowed from the editors this is modelled on, both load-bearing:

* **The map lives on the tab and the widgets are a projection of it**, never the
  other way round. Reading membership back out of the widgets is how
  ``fold_change_pairing_view`` describes losing an assignment to a repopulate.
* **A row edit that rebuilds the rows destroys the widget mid-signal.** The
  combo whose ``currentIndexChanged`` started the edit is deleted by the
  repopulate that edit triggers. Guarded with a re-entrancy flag and a
  zero-delay timer, the same way that editor does it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
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


class AnalyzeBackgroundMapMixin:
    """The Background-well pairing control, its editor, and their validation."""

    #: ``{blank: [wells it corrects]}``, canonical and ordered. The single copy;
    #: every widget below is drawn from it.
    _background_map: dict[str, list[str]]
    #: Guards the repopulate-inside-a-signal re-entrancy described above.
    _bg_map_in_change = False

    # ── the row in the Quality Control section ───────────────────────────
    def _build_background_map_row(self, sec: QWidget) -> None:
        self._background_map = {}
        self._bg_map_active = 0
        self._bg_map_window = None
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
        self._refresh_background_map_summary()

    def _refresh_background_map_summary(self) -> None:
        if not self._background_map:
            self._bg_map_summary.setText(
                "Pooled — every blank corrects every well")
            return
        self._bg_map_summary.setText("; ".join(
            f"{blank} → {len(wells)} well(s)"
            for blank, wells in self._background_map.items()))

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
        self._refresh_background_map_editor()
        self._bg_map_window.show()
        self._bg_map_window.raise_()

    def _build_background_map_window(self) -> QDialog:
        win = QDialog(self)
        win.setWindowTitle("Pair blanks with the wells they correct")
        win.setModal(False)
        outer = QVBoxLayout(win)

        hint = QLabel(
            "Pick a blank below, then drag across the plate to give it the "
            "wells it corrects. Every well must be claimed by exactly one "
            "blank, or the run will refuse to start.")
        hint.setWordWrap(True)
        outer.addWidget(hint)

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

        self._bg_map_uncovered = QLabel("")
        self._bg_map_uncovered.setWordWrap(True)
        outer.addWidget(self._bg_map_uncovered)

        scroll = QScrollArea(win)
        scroll.setWidgetResizable(True)
        self._bg_map_rows_host = QWidget()
        self._bg_map_rows_layout = QVBoxLayout(self._bg_map_rows_host)
        self._bg_map_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._bg_map_rows_layout.addStretch(1)
        scroll.setWidget(self._bg_map_rows_host)
        outer.addWidget(scroll, 1)

        footer = QHBoxLayout()
        add = QPushButton("+ Add blank", win)
        add.setProperty("variant", "secondary")
        add.clicked.connect(self._bg_map_add_blank)
        footer.addWidget(add)

        split = QToolButton(win)
        split.setText("Split plate…")
        split.setPopupMode(QToolButton.InstantPopup)
        split.setStyleSheet(
            "QToolButton::menu-indicator { image: none; width: 0px; }")
        menu = QMenu(split)
        # The blanks are assigned to blocks in the order they were added, which
        # is the order they read in the list — so a user can see which blank got
        # which block rather than having to infer it.
        menu.addAction("One blank per row (A, B, C…)",
                       lambda: self._bg_map_split("row"))
        menu.addAction("One blank per column (01, 02…)",
                       lambda: self._bg_map_split("col"))
        menu.addAction("One blank per quadrant",
                       lambda: self._bg_map_split("quadrant"))
        split.setMenu(menu)
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
            return
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
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    # ── the list of blanks ───────────────────────────────────────────────
    def _bg_map_add_blank(self) -> None:
        """Add a blank, defaulting to a well the map does not already use.

        Seeded from the Background well field when it names one that is free,
        because that is the well the user has already told the form about.
        """
        taken = set(self._background_map)
        for candidate in split_background_wells(
                self._background_well_edit.text()):
            well = canonical_well_label(candidate)
            if well and well not in taken:
                self._background_map[well] = []
                break
        else:
            for well in reversed(self._background_map_plate_wells()):
                if well not in taken:
                    self._background_map[well] = []
                    break
            else:
                return
        self._bg_map_active = len(self._background_map) - 1
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    def _bg_map_remove_blank(self, blank: str) -> None:
        self._background_map.pop(blank, None)
        self._bg_map_active = 0
        self._bg_map_defer_refresh()

    def _bg_map_rename_blank(self, old: str, new_text: str) -> None:
        """Move a blank's whole entry to a different well, keeping its order.

        Rebuilt rather than mutated in place so the list order — which is the
        colour order the user is looking at — does not jump when a blank is
        renamed.
        """
        new = canonical_well_label(new_text)
        if new is None or new == old or new in self._background_map:
            self._bg_map_defer_refresh()
            return
        self._background_map = {
            (new if blank == old else blank):
                [w for w in wells if w != new]
            for blank, wells in self._background_map.items()
        }
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
        self._background_map = {}
        self._bg_map_active = 0
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()

    def _bg_map_split(self, how: str) -> None:
        """Replace the map with one blank per row / column / quadrant.

        The blocks come from the plate's own wells, so a plate that is only half
        populated gets half-blocks rather than blocks of absent wells. Blanks
        are taken from the Background well field first and then from the
        unclaimed wells, and a split that cannot find enough blanks makes as many
        blocks as it has blanks for rather than silently dropping the rest.
        """
        wells = self._background_map_plate_wells()
        if how == "row":
            keys = sorted({w[0] for w in wells})
            blocks = [[w for w in wells if w[0] == k] for k in keys]
        elif how == "col":
            keys = sorted({w[1:] for w in wells})
            blocks = [[w for w in wells if w[1:] == k] for k in keys]
        else:
            def quadrant(w: str) -> tuple[bool, bool]:
                return (w[0] >= "E", int(w[1:]) > 6)
            keys = sorted({quadrant(w) for w in wells})
            blocks = [[w for w in wells if quadrant(w) == k] for k in keys]

        # Blanks come only from the Background well field, never from the
        # plate's remaining wells. Inventing one turns a sample well into a
        # reference — silently, and against the whole point of pairing — and
        # the first draft of this did exactly that: it took H12, H11, H10… off
        # the end of the plate, so row H's blank sat inside row H's own block
        # and was corrected by itself.
        blanks = [w for w in (canonical_well_label(c) for c
                              in split_background_wells(
                                  self._background_well_edit.text())) if w]
        if not blanks:
            self._bg_map_uncovered.setText(
                "Name the blank well(s) in Background well first — a split "
                "assigns the blanks you have, and will not turn a sample well "
                "into a reference.")
            return

        # Every chosen blank is held out of *every* block, not just its own.
        chosen = blanks[:len(blocks)]
        held_out = set(chosen)
        mapping: dict[str, list[str]] = {}
        for blank, block in zip(chosen, blocks):
            claimed = sorted(w for w in block if w not in held_out)
            if claimed:
                mapping[blank] = claimed
        self._background_map = mapping
        self._bg_map_active = 0
        self._refresh_background_map_editor()
        self._refresh_background_map_summary()
        if len(blanks) < len(blocks):
            self._bg_map_uncovered.setText(
                f"{len(blanks)} blank(s) for {len(blocks)} block(s) — the rest "
                f"of the plate is unclaimed. Name more blanks in Background "
                f"well, or paint the remaining wells onto a blank above.")

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

    def _bg_map_refresh_uncovered(self) -> None:
        """Say how many wells nobody claims — the run's own refusal, in advance.

        Shown whether or not it is zero, and in the affirmative when it is:
        "0 uncovered" and a missing line look identical, and only one of them
        means the map is finished.
        """
        from pipeline.empty_well import BlankGroups

        if not self._background_map:
            self._bg_map_uncovered.setText(
                "Pooled — no pairing in force. Every blank corrects every well.")
            return
        groups = BlankGroups("", self._background_map_value(),
                             corrections=("empty_well",))
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
        """One row per blank: its colour, its well, its count, and a remove."""
        layout = self._bg_map_rows_layout
        while layout.count() > 1:                 # keep the trailing stretch
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        wells = self._background_map_plate_wells()
        for index, blank in enumerate(self._bg_map_blanks()):
            layout.insertWidget(index, self._bg_map_build_row(index, blank, wells))

    def _bg_map_build_row(self, index: int, blank: str,
                          wells: list[str]) -> QWidget:
        row = QWidget(self._bg_map_rows_host)
        line = QHBoxLayout(row)
        line.setContentsMargins(2, 1, 2, 1)

        swatch = QLabel(row)
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(
            f"background:{self._bg_map_color(index)};border-radius:3px;")
        line.addWidget(swatch)

        pick = QToolButton(row)
        pick.setText("●" if index == self._bg_map_active else "○")
        pick.setToolTip("Paint onto this blank's block")
        pick.clicked.connect(lambda _=False, i=index: self._bg_map_set_active(i))
        line.addWidget(pick)

        combo = QComboBox(row)
        combo.addItems(wells if blank in wells else [blank] + wells)
        combo.setCurrentText(blank)
        combo.currentTextChanged.connect(
            lambda text, old=blank: self._bg_map_rename_blank(old, text))
        line.addWidget(combo)

        count = QLabel(f"{len(self._background_map[blank])} well(s)", row)
        count.setObjectName("Muted")
        line.addWidget(count)
        line.addStretch(1)

        remove = QToolButton(row)
        remove.setText("✕")
        remove.setToolTip("Remove this blank from the pairing")
        remove.clicked.connect(lambda _=False, b=blank: self._bg_map_remove_blank(b))
        line.addWidget(remove)
        return row

    def _bg_map_set_active(self, index: int) -> None:
        self._bg_map_active = index
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
        return self._background_map_coverage_error(mapping)

    def _background_map_coverage_error(self, mapping: str) -> str | None:
        """The uncovered-well refusal, asked of the form rather than the run."""
        if not mapping:
            return None
        from pipeline.empty_well import BlankGroups

        groups = BlankGroups("", mapping, corrections=("empty_well",))
        uncovered = groups.uncovered(self._background_map_plate_wells())
        if not uncovered:
            return None
        return (f"{len(uncovered)} well(s) are not paired with any blank:\n"
                f"{', '.join(uncovered)}\n\n"
                "Every well the run processes needs a blank, because a well "
                "corrected by the wrong block's reference produces numbers that "
                "look exactly like correct ones. Open Pair blanks… to claim "
                "them, or clear the pairing to pool every blank instead.")
