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

from PySide6.QtWidgets import QLabel, QWidget

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
            row_factory=self._row,
            combo_width=self._CB_W,
            schema_edit_min_width=self._SCHEMA_STR_EDIT_MIN_W,
        )
        sec.layout().addWidget(self._schema_picker)
        self._schema_picker.changed.connect(self._refresh_schema_preview)

        self._schema_err_lbl = QLabel("", sec)
        self._schema_err_lbl.setObjectName("Error")
        self._schema_err_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_err_lbl)

        self._schema_preview_lbl = QLabel("", sec)
        self._schema_preview_lbl.setObjectName("Muted")
        self._schema_preview_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_preview_lbl)

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
        errors = self._schema_errors()
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
