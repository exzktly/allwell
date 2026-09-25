"""The Analyze form's two per-well scope fields.

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

ONLY_WELLS_TIP = (
    "Comma-separated wells to process — and no others. Every other well's "
    "output, and its records in pipeline_info.json, are left exactly as they "
    "are. The run refuses to start if a named well is not in the input. Empty "
    "means the whole plate.\n"
    "Combine with Incremental to finish partial wells, and with Force these "
    "wells to regenerate named ones. This field is never restored from a "
    "previous run's settings: a subset is a decision for this run only.")

FORCE_WELLS_TIP = (
    "Comma-separated wells to reprocess in full, whatever else the run "
    "decides. A well whose <WELL>_out.zip exists but whose measurements "
    "CSV is missing looks complete to an incremental run — its archive "
    "members are counted as already done — so only forcing it "
    "regenerates the CSV. Re-segments those wells and overwrites their "
    "outputs; everything else is unaffected.")


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
