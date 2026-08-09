"""Qt builder for the Import Loose Folder page.

A source-folder picker, the shared filename-schema builder
(:class:`~widgets.schema_picker.SchemaPicker`, the same control the Analyze form
uses, since this page parses the very filenames Analyze will go on to read), a
live preview readout (wells × FOVs × timepoints × channels, plus unparsed/
unreadable counts and the names of a couple of files that did not match) that
updates on every keystroke from one scan shared with the plate, a destination-folder
picker (hidden when "group in place" is checked), and the usual
Analyze-style status section — a per-well plate map
(:class:`~datasets.import_loose.dashboard.ImportLooseDashboard`) with the raw
log demoted to a collapsible checkbox, the same shape
``datasets/concatenate/view.py`` and ``datasets/subset/view.py`` use.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.import_loose.controller`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from datasets._common.log_panel import LogPanelMixin, hrule as _hrule
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker
from widgets.schema_picker import SchemaPicker
from datasets._common.dataset_fields import is_free_destination
from datasets._common.dataset_paths import (
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
)
from datasets.import_loose.dashboard import ImportLooseDashboard
from datasets.import_loose.import_loose_compute import scan_folder


#: A marker this tool never writes. The shared destination rule exempts a folder
#: holding *this* tool's own interrupted attempt, because for the tools that
#: write one that is a resume rather than a collision. Zipping a loose folder has
#: no resume, so nothing is exempt and any content blocks — which is what passing
#: a name that never appears on disk expresses.
_NO_RESUME_MARKER = ".aw_import_loose_in_progress"


def _is_destination(root: Path) -> str:
    """Return "" if *root* is usable as a new-destination target, else a short
    reason — the page-side early warning for the same guard
    ``apply.py::_check_new_destination`` enforces authoritatively before any
    write happens.

    The shared rule (`_common.dataset_fields.is_free_destination`), the same one
    Concatenate, Subset and Merge Plates use. Its wording is shared on purpose:
    a user moving between two Datasets tools should not be told the same thing
    two different ways. Checking only `in`/`out` here, as this page used to, let
    a destination full of unrelated files through.
    """
    return is_free_destination(
        root,
        is_complete=destination_is_complete_dataset,
        has_unrecognized=lambda dest: destination_has_unrecognized_content(
            dest, _NO_RESUME_MARKER),
    )


class ImportLoosePage(LogPanelMixin, QWidget):
    """The Import Loose Folder feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInAnalyzeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        #: The last scan, so the run gate can refuse what it can already see
        #: will produce nothing. ``None`` means "not scanned yet", which is not
        #: the same as "scanned and found nothing" and must not gate the run.
        self._preview = None
        #: Set once an in-place run has succeeded here. The source folder now
        #: holds archives rather than loose images, so a second run has nothing
        #: to do and would replace this run's result with a failure.
        self._done_in_place = False
        self._build_ui()
        self.refresh_readout()

    def _build_ui(self) -> None:
        # Wrap in a scroll area — see datasets/concatenate/view.py's
        # _build_ui for why (the dashboard's plate map used to render
        # illegibly small at the app's default window height).
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Zip Loose Folder", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Sort a folder of loose, ungrouped TIFs into one archive per well — "
            "the layout Analyze reads. By default it writes a new folder and "
            "leaves your images where they are; tick <b>Group in place</b> to "
            "sort them where they sit and delete the loose originals instead. "
            "Analyze does this for you when it meets a folder of loose TIFs, in "
            "place and without asking, so use this page when you want to check "
            "the split first or keep the originals.",
            self,
        )
        blurb.setTextFormat(Qt.RichText)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField(
            "Loose-TIF folder", pick=folder_picker("Select folder of loose TIFs"))
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)

        # The seven rows below are the same control the Analyze form's Filename
        # Schema section uses, and there they sit under that section's title.
        # Dropped straight onto a page they are seven unlabelled rows of jargon
        # between a folder picker and a checkbox, with nothing saying they
        # describe *filenames* or what a "position" is.
        schema_heading = QLabel("How are the filenames laid out?", self)
        schema_heading.setObjectName("Heading")
        outer.addWidget(schema_heading)

        schema_hint = QLabel(
            "Positions are the chunks between separator characters. In "
            "<tt>Exp01_GFP_A01_F001_24h.tif</tt> the separator is <tt>_</tt>, "
            "position 1 is the experiment and position 3 is the well.",
            self,
        )
        schema_hint.setObjectName("Muted")
        schema_hint.setTextFormat(Qt.RichText)
        schema_hint.setWordWrap(True)
        outer.addWidget(schema_hint)

        # This page parses the very filenames Analyze will go on to read, so
        # asking for the schema a different way here — two bare text fields
        # against Analyze's dropdowns — meant the user typed by hand what they
        # pick from a list one pane over, with no field vocabulary in front of
        # them and nothing to say which spellings the pipeline understands.
        self._schema_picker = SchemaPicker(self)
        self._schema_picker.changed.connect(self._on_readout_input_changed)
        outer.addWidget(self._schema_picker)

        self._schema_err = QLabel("", self)
        self._schema_err.setObjectName("Error")
        self._schema_err.setWordWrap(True)
        self._schema_err.setVisible(False)
        outer.addWidget(self._schema_err)

        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        outer.addWidget(self._readout)

        self._in_place_cb = QCheckBox(
            "Group in place (delete loose TIFs on success)", self)
        self._in_place_cb.toggled.connect(self._on_in_place_toggled)
        outer.addWidget(self._in_place_cb)

        self._in_place_warning = QLabel(
            "This deletes the original loose TIFs once each well is safely "
            "zipped. This can't be undone.", self,
        )
        self._in_place_warning.setObjectName("Danger")
        self._in_place_warning.setWordWrap(True)
        self._in_place_warning.setVisible(False)
        outer.addWidget(self._in_place_warning)

        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=_is_destination)
        self._dest_field.changed.connect(self._refresh_run_enabled)
        outer.addWidget(self._dest_field)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Zip folder", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        run_row_l.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this run to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        run_row_l.addWidget(self._queue_btn)
        run_row_l.addStretch(1)
        outer.addWidget(run_row, 0, Qt.AlignLeft)

        self._build_status_section(outer)

        self._open_btn = QPushButton("Open dataset in Analyze", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInAnalyzeRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

        # A trailing stretch outranks every 0-stretch item above it for any
        # leftover space the QScrollArea's content widget picks up filling
        # the viewport (see widgets/plate_progress.py's own Maximum policy
        # fix for why the dashboard itself no longer eats that space) — so
        # slack collects as blank page background below everything instead
        # of inflating a label, field, or the dashboard's own plate map.
        outer.addStretch(1)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        self.build_log_header(outer, "Zip Output")

        self.dashboard = ImportLooseDashboard(self)
        # Hidden until the source folder scans to at least a real answer —
        # _update_status_preview() reveals it seeded with the wells the scan
        # actually found, and ImportLooseController takes over colouring it
        # once a run actually starts.
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self.build_log_tail(outer, object_name="ImportLooseStatus", placeholder="Status will appear here.")

    # ── public API used by the controller ──────────────────────────────
    def source_path(self) -> str:
        return self._src_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def filename_schema(self) -> str:
        return self._schema_picker.schema() or DEFAULT_SCHEMA

    def filename_sep(self) -> str:
        return self._schema_picker.separator()

    def in_place(self) -> bool:
        return self._in_place_cb.isChecked()

    def set_source_path(self, path: str) -> None:
        self._src_field.set_path(path)

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def set_in_place(self, in_place: bool) -> None:
        self._in_place_cb.setChecked(bool(in_place))

    def set_filename_schema(self, schema: str) -> None:
        self._schema_picker.setSchema(schema or DEFAULT_SCHEMA)

    def set_filename_sep(self, sep: str) -> None:
        self._schema_picker.setSeparator(sep or DEFAULT_SEP)

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running and self._can_run())
        self._queue_btn.setEnabled(not running and self._can_run())
        self._run_btn.setText("Zipping…" if running else "▶  Zip folder")
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")
        # Locked alongside the run (ImportLooseController._launch captures
        # source_path() once at launch and never re-reads it afterward, but
        # the field itself must not drift out from under the user mid-run
        # either — same class of bug as Subset's source/destination fields).
        # setLocked(), not setEnabled(False) — see PathField.setLocked.
        self._src_field.setLocked(running)
        self._dest_field.setLocked(running)
        self._schema_picker.setEnabled(not running)
        self._in_place_cb.setEnabled(not running)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(bool(dest_root))
        if dest_root:
            # Promoted to the primary action on success, demoting Convert —
            # see ConcatenatePage.show_open_in_review for the reasoning.
            set_variant(self._open_btn, "primary")
            set_variant(self._run_btn, "secondary")

    def revalidate_fields(self, *, ok: bool = False) -> None:
        """Re-run the destination field's validator against current disk
        state, called once a run finishes (mirrors
        ConcatenatePage.revalidate_fields / MergePlatesPage.revalidate_fields).

        Without this, a new-destination run's own success silently disabled
        a second Convert into the same folder — the destination genuinely
        now has content, but nothing had ever re-run the validator to say
        so, so the button just went dark with no visible reason. *ok*: swap
        the validator's alarming "already has files" wording for a neutral
        note, since that's the expected result of the run that just
        succeeded, not a problem with the pick.
        """
        if self.in_place():
            return
        self._dest_field.revalidate()
        if ok and self._dest_field.message():
            self._dest_field.show_note(
                "This destination now holds the zipped dataset from "
                "that run — pick a new folder to zip again."
            )
        self._refresh_run_enabled()

    def refresh_readout(self, preview=None) -> None:
        """Describe what the current folder + schema would produce.

        *preview* lets a caller that has already scanned pass the result in;
        every input change updates this readout and the plate preview together,
        and scanning twice for one keystroke walks a whole plate's worth of
        files twice on the GUI thread.
        """
        if self._done_in_place:
            # Re-scanning here would walk a folder whose loose images are now
            # archives and report that nothing matches the schema — directly
            # above this run's own green "Grouped loose TIFs … into N zip(s)".
            # Say what is actually true of the folder instead.
            self._preview = None
            self._readout.setText(
                "This folder now holds per-well archives. Pick another folder "
                "of loose TIFs to zip.")
            self._refresh_run_enabled()
            return
        src = self.source_path()
        if not src or not Path(src).is_dir():
            self._preview = None
            self._readout.setText("Pick a folder to preview.")
            self._refresh_run_enabled()
            return
        if preview is None:
            preview = self._scan()
        self._preview = preview
        lines = [preview.summary()]
        if preview.unreadable_count:
            lines.append(f"{preview.unreadable_count} unreadable file(s) (broken symlinks) skipped.")
        if preview.unparsed_files and preview.matched:
            # Only worth saying when some files *did* match — with nothing
            # matched the summary has already said so, and "would be left
            # alone" is reassuring phrasing for a total failure. Naming two of
            # them is the fastest way to spot a wrong separator.
            examples = ", ".join(Path(p).name for p in preview.unparsed_files[:2])
            more = "" if len(preview.unparsed_files) <= 2 else ", …"
            lines.append(
                f"{len(preview.unparsed_files)} of {preview.examined} file(s) didn't match the "
                f"schema and would be left alone (e.g. {examples}{more})."
            )
        # Each clause is its own sentence; without the stop they run together
        # ("… channels: GFP 1 of 3 file(s) didn't match …").
        self._readout.setText(" ".join(
            line if line.endswith((".", "!", "?")) else line + "." for line in lines))
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _scan(self):
        """One walk of the source folder with the current schema + separator."""
        return scan_folder(
            self.source_path(),
            filename_schema=self.filename_schema(),
            filename_sep=self.filename_sep(),
        )

    def _on_readout_input_changed(self, *_args) -> None:
        # One scan feeds both the readout and the plate. They ask the same
        # question of the same folder with the same arguments, and the schema
        # and separator fields are both live per keystroke.
        src = self.source_path()
        preview = self._scan() if src and Path(src).is_dir() else None
        self.refresh_readout(preview)
        self._update_status_preview(preview)

    def _update_status_preview(self, preview=None) -> None:
        # Deliberately separate from refresh_readout() -- ImportLooseController
        # ._on_finished calls refresh_readout() right after a run completes
        # (to refresh the readout text/run-gate against any now-changed
        # destination state), and by then the dashboard already holds that
        # run's own final result (set_done()/set_failed(), called before
        # _on_finished). Folding this into refresh_readout() would
        # immediately overwrite that with a plain pending-coloured preview.
        # Wired instead to the specific interactive-change handlers, none of
        # which fire mid-run (every relevant field is locked for the run's
        # duration) or as part of run completion.
        src = self.source_path()
        if not src or not Path(src).is_dir():
            self.dashboard.setVisible(False)
            return
        if preview is None:
            preview = self._scan()
        if not preview.wells:
            # Nothing the current schema/separator recognises yet -- an
            # empty "Ready — 0 well(s)" plate would answer a question
            # ("what will this zip?") with "nothing", which reads the
            # same as "broken" rather than "not ready yet".
            self.dashboard.setVisible(False)
            return
        self.dashboard.reset()
        self.dashboard.set_wells(preview.wells)
        self.dashboard.set_headline(f"Ready — {len(preview.wells)} well(s) to zip.")
        self.dashboard.setVisible(True)

    def _on_source_changed(self) -> None:
        set_variant(self._run_btn, "primary")
        self._done_in_place = False        # a different folder, a fresh verdict
        self._on_readout_input_changed()

    def confirm_counts(self) -> str:
        """The opening clause of the in-place confirmation — what this run will
        actually touch, from the scan the page already has.

        Ends with a trailing space so the caller can follow it with "directly
        in:". Falls back to a countless phrasing if nothing has been scanned,
        rather than asserting a zero it has not measured.
        """
        preview = self._preview
        if preview is None or not preview.wells:
            return "This will group the loose TIFs "
        return (f"This will zip {preview.matched} file(s) into "
                f"{len(preview.wells)} well archive(s) ")

    def unmatched_note(self) -> str:
        """A sentence naming what the run will *not* touch, or "".

        The readout says non-matching files are left alone; someone consenting
        to a delete should not have to remember that from another part of the
        page."""
        preview = self._preview
        if preview is None or not preview.unparsed_files:
            return ""
        count = len(preview.unparsed_files)
        return (f"{count} file(s) that don't match the schema are left "
                "where they are.")

    def mark_in_place_done(self) -> None:
        """Record that an in-place run finished here, so the page stops offering
        to do it again. The source folder now holds archives rather than loose
        images: a second run finds nothing to group, fails, and replaces the
        first run's result — including the *Open dataset in Analyze* handoff —
        with a red failure the user caused by pressing an enabled button."""
        self._done_in_place = True
        self.refresh_readout()

    def _on_in_place_toggled(self, checked: bool) -> None:
        # Disabled, not removed from the layout (UX audit P2-6) — the field
        # disappearing outright when the checkbox is toggled used to be the
        # only signal a destructive mode had just been armed; a layout jump
        # is easy to miss, especially since nothing else on the page changes
        # at the same moment. Kept visible-but-inert instead, right beside
        # an explicit inline warning that states what checking the box does.
        self._dest_field.setEnabled(not checked)
        self._in_place_warning.setVisible(checked)
        # The destination's validator message goes with the field. Left behind
        # it is a red "choose an empty folder" under a greyed-out box, beside a
        # live Run button that does not use the destination at all.
        if checked:
            self._dest_field.show_note("")
        else:
            self._dest_field.revalidate()
        self._refresh_run_enabled()

    def _can_run(self) -> bool:
        src = self.source_path()
        if not src or not Path(src).is_dir():
            return False
        # The same check the run itself makes, moved to where the schema is
        # typed. A schema with no "well" cannot say which archive a file belongs
        # to and one with two cannot choose, so the scan examines every file and
        # groups none — a tidy zero with no reason attached. In-place runs fail
        # harder still: they shell out to WellPlateZipper.py, which exits 2 on
        # exactly these errors. And a schema with no "channel" produces archives
        # Analyze cannot then read, which is the only reason to build them.
        if self._schema_picker.errors():
            return False
        # The page has already scanned and knows the answer is zero. Offering a
        # run it can see will fail spends the user's click to tell them what the
        # readout above the button already says. Subset gates the same way on
        # its selected wells, Concatenate on its cross-field message.
        if self._preview is not None and not self._preview.wells:
            return False
        if self.in_place():
            return not self._done_in_place
        if self._same_folder_message():
            return False
        return bool(self.destination_path()) and self._dest_field.is_valid()

    def _same_folder_message(self) -> str:
        """Why the destination cannot be the source, or "".

        A cross-field rule, so it lives on the page rather than in the shared
        validator, which cannot see two fields at once — the same split
        Concatenate and Subset make. Writing into the source leaves the folder
        half loose images and half dataset, with the originals undeleted:
        neither what the new-destination mode promises nor what *Group in
        place* does.
        """
        src, dest = self.source_path(), self.destination_path()
        if not src or not dest:
            return ""
        try:
            same = Path(src).resolve() == Path(dest).resolve()
        except OSError:
            same = str(src) == str(dest)
        if same:
            return ("Destination must be a different folder from the source — "
                    "or tick Group in place.")
        return ""

    def _refresh_run_enabled(self) -> None:
        errors = self._schema_picker.errors()
        self._schema_err.setText("  ".join(errors))
        self._schema_err.setVisible(bool(errors))
        # A gate the user cannot see is a button that went dark for no reason.
        # The schema and destination rules each show their own message; this one
        # spans two fields, so it goes on the destination, where the fix is.
        same = self._same_folder_message()
        if same and not self.in_place():
            self._dest_field.show_warning(same)
        can_run = self._can_run()
        self._run_btn.setEnabled(can_run)
        self._queue_btn.setEnabled(can_run)
