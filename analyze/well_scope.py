"""The Analyze form's **Run scope** section: what to do with wells that
already have output, and which wells to run.

**Existing output** is one three-way choice — skip such a well, fill in what it
is missing, or redo it — and is the pipeline's ``--incremental`` / ``--force``
pair. It was two checkboxes, which allowed a fourth state the pipeline resolved
silently (``--force`` wins, ``--incremental`` is ignored) and which a tooltip
had to explain.

Then the two per-well fields.

*Only these wells* narrows a run to a subset of the plate
(``process_microscopy --only_wells``); *Force these wells* re-segments named
wells from scratch (``--force_wells``). Both are visible fields rather than
hidden run state, because both change what a run does to wells the user may not
be looking at, so they must never be something the user cannot see or clear.
The two "complete this run" handoffs (Datasets → Validate & Repair, Review →
Home) fill them with the wells that still need work.

Held apart from ``analyze/form.py`` for the reason ``analyze/qc_section.py``
is: that file sits at its line budget.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QWidget

from analyze.domain.well_list import parse_well_list
from widgets.value_combo import ValueCombo

#: The existing-output choice, ``(value, label)``. ``skip`` is the pipeline's
#: default (neither flag); ``incremental`` and ``force`` are its two flags.
EXISTING_OUTPUT_OPTIONS = (
    ("skip", "Skip it"),
    ("incremental", "Fill in what's missing"),
    ("force", "Redo it"),
)

EXISTING_OUTPUT_TIP = (
    "What a run does to a well whose output is already in the output folder.\n"
    "• Skip it (default): leave the well as it is.\n"
    "• Fill in what's missing: process only the fields and timepoints its "
    "output lacks and merge them in — how a crashed or partial run is "
    "finished.\n"
    "• Redo it: process every well from scratch and overwrite its output.")

ONLY_WELLS_TIP = (
    "Comma-separated wells to process — and no others. Every other well's "
    "output, and its records in pipeline_info.json, are left exactly as they "
    "are. The run refuses to start if a named well is not in the input. Empty "
    "means the whole plate.\n"
    "Combine with Fill in what's missing to finish partial wells, and with Force these "
    "wells to regenerate named ones. This field is never restored from a "
    "previous run's settings: a subset is a decision for this run only.")

FORCE_WELLS_TIP = (
    "Comma-separated wells to reprocess in full, whatever else the run "
    "decides. A well whose <WELL>_out.zip exists but whose measurements "
    "CSV is missing looks complete to an incremental run — its archive "
    "members are counted as already done — so only forcing it "
    "regenerates the CSV. Re-segments those wells and overwrites their "
    "outputs; everything else is unaffected.")


class AnalyzeRunScopeMixin:
    """The Run scope section's builder. Binds to the ``AnalyzeTab``."""

    def _build_run_scope_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Run scope", collapsible=True, collapsed=True,
                            summary=self._run_scope_summary)
        row = self._row(sec, "If a well already has output", tooltip=EXISTING_OUTPUT_TIP)
        self._existing_output_cb = ValueCombo(EXISTING_OUTPUT_OPTIONS, row)
        self._existing_output_cb.setToolTip(EXISTING_OUTPUT_TIP)
        row.layout().addWidget(self._existing_output_cb, 1)
        build_well_scope_rows(self, sec)

    def _run_scope_summary(self) -> str:
        """The folded header's line: anything that is not "every well, skip
        what is done"."""
        parts = []
        choice = self._existing_output_cb.value()
        if choice != "skip":
            parts.append(self._existing_output_cb.currentText())
        only = self._only_wells_edit.text().strip()
        if only:
            parts.append(f"only {only}")
        forced = self._force_wells_edit.text().strip()
        if forced:
            parts.append(f"redo {forced}")
        return " · ".join(parts)

    def _set_existing_output(self, *, force=None, incremental=None) -> None:
        """Set the choice from the pipeline's two flags, as a sidecar records
        them: ``force`` wins, as it does in the pipeline. ``None`` for both
        leaves the choice alone."""
        if force is None and incremental is None:
            return
        if force:
            self._existing_output_cb.setValue("force")
        elif incremental:
            self._existing_output_cb.setValue("incremental")
        else:
            self._existing_output_cb.setValue("skip")


def build_well_scope_rows(form, sec: QWidget) -> None:
    """Add the two rows to *sec*, storing ``_only_wells_edit`` and
    ``_force_wells_edit`` on *form* (the ``AnalyzeTab``)."""
    for label, attr, tip in (("Only these wells", "_only_wells_edit", ONLY_WELLS_TIP),
                             ("Force these wells", "_force_wells_edit", FORCE_WELLS_TIP)):
        row = form._row(sec, label)
        edit = QLineEdit(row)
        edit.setPlaceholderText("(all wells)" if attr == "_only_wells_edit" else "(none)")
        edit.setToolTip(tip)
        row.layout().addWidget(edit, 1)
        setattr(form, attr, edit)


def well_scope_options(form) -> dict:
    """The run options the two fields contribute."""
    return {"only_wells": parse_well_list(form._only_wells_edit.text()),
            "force_wells": parse_well_list(form._force_wells_edit.text())}
