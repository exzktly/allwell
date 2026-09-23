"""The Analyze form's **Filename Schema** section: its widgets and its preview.

One section of the form, gathered into one module. The controls were built in
``analyze/form.py`` and the preview that refreshes them lived in
``analyze/tab.py``, which put the widget and the one function that writes to it
in different files under different line budgets — and both budgets are tight
enough that the section could not grow in either place.

The separator, the five position dropdowns and the schema string they write are
``widgets.schema_picker.SchemaPicker``, shared with the Datasets pane's Zip
Loose Folder page because that page asks the user the same question about the
same filenames. What is genuinely Analyze's is the preview beneath it, which is
drawn from the channel tokens entered further down this same form, and the
detection readout, which states what the folder scan derived and how much of
the folder it actually parses.

Mixed into ``AnalyzeTabFormMixin`` the way ``analyze.seg_params`` is, so every
widget still lands on the ``AnalyzeTab`` instance under the same
``self._<name>`` attribute it always had: ``analyze/pinfo.py`` reaches
``_schema_picker`` and calls ``_refresh_schema_preview`` by name, and
``analyze/bg_map.py`` and ``analyze/form.py`` read the separator off the picker.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel, QWidget

from widgets.schema_picker import SchemaPicker

#: Stand-in tokens for the "e.g. ..." line, one per schema field. The channel
#: slot is filled from the form's own channel tokens instead.
_PREVIEW_TOKENS = {
    "experiment": "Exp01",
    "well":       "B03",
    "fov":        "F001",
    "timepoint":  "02d04h30m",
    "ignore":     "X",
}


class AnalyzeSchemaSectionMixin:
    """Builds the Filename Schema section and keeps its preview current."""

    def _build_schema_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Filename Schema")

        # The separator, the five position dropdowns and the schema string they
        # write are one reusable control (`widgets.schema_picker`), because the
        # Datasets pane's Zip Loose Folder page asks the user the same question
        # about the same filenames. Passing `self._row` in keeps it inside this
        # form's label column and general look.
        self._schema_picker = SchemaPicker(
            sec,
            detect_label="Detect from filenames",
            row_factory=self._row,
            combo_width=self._CB_W,
            schema_edit_min_width=self._SCHEMA_STR_EDIT_MIN_W,
        )
        sec.layout().addWidget(self._schema_picker)
        self._schema_picker.changed.connect(self._refresh_schema_preview)
        self._schema_picker.detectRequested.connect(self._detect_schema_from_input)
        # A standing line, so the control says what it is for before it is
        # pressed. Without it the button is live from launch, can only produce
        # a refusal until a folder is chosen, and teaches the user in their
        # first ten seconds that the feature does not work. The Zip Loose
        # Folder page has always carried the equivalent line.
        self._schema_picker.setDetection(
            "Pick an input folder below, then detect its layout from the "
            "filenames.")

        # Run-time detection: the same scan the button above makes, made again
        # when the run starts — for a queued job whose input an earlier job
        # has not written yet, so there is nothing to scan now. The values it
        # finds replace the schema above and the channel tokens below, which is
        # why the picker greys out: its contents are no longer what will run.
        self._auto_detect_cb = QCheckBox(
            "Detect schema && channels at run time", sec)
        self._auto_detect_cb.setToolTip(
            "For a queued run whose input does not exist yet. When the run "
            "starts, the filename schema, the channels and which channel is "
            "segmented are read from the input's filenames — or taken from the "
            "converter this job runs after — replacing what this form shows. "
            "If they cannot be read, the run fails and says why. smFISH marks, "
            "the cytoplasm channel and the Cellpose nuclei channel are kept, "
            "and must name channels the input carries.")
        self._auto_detect_cb.toggled.connect(self._on_auto_detect_toggled)
        sec.layout().addWidget(self._auto_detect_cb)

        self._schema_err_lbl = QLabel("", sec)
        self._schema_err_lbl.setObjectName("Error")
        self._schema_err_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_err_lbl)

        self._schema_preview_lbl = QLabel("", sec)
        self._schema_preview_lbl.setObjectName("Muted")
        self._schema_preview_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_preview_lbl)

    def _on_auto_detect_toggled(self, on: bool) -> None:
        """Grey out what run-time detection replaces, and re-check the form.

        Only the schema picker and the segmentation-channel row are disabled.
        The fluorescent rows stay live because their smFISH marks are kept
        through detection — they are checked against the detected channels
        when the run starts rather than replaced.
        """
        self._schema_picker.setEnabled(not on)
        row = getattr(self, "_nuclear_row_widget", None)
        if row is not None:
            row.setEnabled(not on)
        self._refresh_schema_preview()
        refresh_hints = getattr(self, "_refresh_segmentation_hints", None)
        if callable(refresh_hints):
            refresh_hints()

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------
    # The separator / dropdowns / schema-string control is
    # ``widgets.schema_picker.SchemaPicker``, shared with the Datasets pane's
    # Zip Loose Folder page. These read through to it; the preview below is the
    # part that is genuinely Analyze's, since it is drawn from the channel
    # tokens entered further down this same form.
    def _schema_field_list(self) -> list[str]:
        return self._schema_picker.fields()

    def _schema_errors(self) -> list[str]:
        return self._schema_picker.errors()

    def _build_schema_arg(self) -> str:
        return self._schema_picker.schema()

    def _filename_sep(self) -> str:
        return self._schema_picker.separator()

    def _refresh_schema_preview(self) -> None:
        if not hasattr(self, "_schema_picker") or not hasattr(self, "_schema_err_lbl"):
            return
        # A schema detection will replace is not one that has to be valid now:
        # refusing it would lock the input field this very option exists to
        # let the user fill with a folder that has no files yet.
        detect = self._auto_detect_on() if hasattr(self, "_auto_detect_cb") else False
        errors = [] if detect else self._schema_errors()
        fields = self._schema_field_list()
        sep = self._filename_sep()
        schema_valid = not errors

        if hasattr(self, "_input_edit"):
            self._input_edit.setEnabled(schema_valid)
            self._browse_btn.setEnabled(schema_valid)
        if hasattr(self, "_folder_lock_lbl"):
            self._folder_lock_lbl.setText("" if schema_valid else "Define the filename schema above first.")

        if errors:
            self._schema_err_lbl.setText("  ".join(errors))
            self._schema_preview_lbl.setText("")
            if hasattr(self, "_run_btn"):
                self._run_btn.setEnabled(False)
            return

        self._schema_err_lbl.setText("")
        if detect:
            self._schema_preview_lbl.setText(
                "Read from the input's filenames when the run starts.")
            if hasattr(self, "_run_btn"):
                self._run_btn.setEnabled(not self._running)
            return
        if hasattr(self, "_run_btn"):
            # A valid schema is necessary, not sufficient. This runs from four
            # controls that stay live during a run — the segmentation-channel
            # edit, every fluorescent row's edit, add/remove row, and the
            # schema picker — so enabling unconditionally re-armed Run while a
            # plate was still going, and pressing it started a second pipeline
            # into the same output directory. `_set_running_ui_state` owns the
            # other half of this flag; both must agree.
            self._run_btn.setEnabled(not self._running)

        nuclear_tok = (self._nuclear_token_edit.text().strip() if hasattr(self, "_nuclear_token_edit") else "NIR") or "NIR"
        fluor_toks  = self._fluor_tokens_list() if self._fluor_rows else ["GFP"]
        fluor_toks  = fluor_toks or ["GFP"]

        def _make_example(chan_tok: str) -> str:
            parts = [chan_tok if f == "channel" else _PREVIEW_TOKENS.get(f, "X") for f in fields]
            return sep.join(parts) + ".tif"

        lines = [f"e.g. {_make_example(nuclear_tok)}"] + [f"     {_make_example(t)}" for t in fluor_toks[:2]]
        self._schema_preview_lbl.setText("\n".join(lines))

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def _detect_schema_from_input(self) -> None:
        """Re-run the folder scan against whatever the input field names now.

        The same call browsing to a folder makes, deliberately: two ways of
        asking one question are two answers waiting to disagree, and the scan is
        already bounded to a sample of the names rather than a walk. What the
        button adds is the ability to ask **again** — after typing over the
        guess, after fixing the separator by hand, after pointing at a different
        folder in the text field rather than through the browser.

        An empty field is its own answer and says so, rather than scanning the
        process's working directory.
        """
        folder = self._input_edit.text().strip() if hasattr(self, "_input_edit") else ""
        if not folder:
            self._set_schema_detection(
                "Choose an input folder first — there are no filenames to read.",
                problem=True)
            return
        self._autofill_from_input_scan(folder)

    def _set_schema_detection(self, text: str, *, note: str = "",
                              problem: bool = False) -> None:
        """Put *text* under the picker as the result of the last detection.

        Guarded, because the scan runs from ``_browse_input`` as well as from
        the button, and the browse path can fire before this section is built
        when a folder arrives on the command line.
        """
        picker = getattr(self, "_schema_picker", None)
        if picker is not None:
            picker.setDetection(text, note=note, problem=problem)
