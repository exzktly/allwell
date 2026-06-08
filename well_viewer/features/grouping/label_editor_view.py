"""Well label editor builder (Qt port)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import btn_secondary


def build_label_editor(app, parent: QWidget) -> None:
    """Centre panel of Sample Definitions tab: assign custom labels."""
    from well_viewer.core.ui_helpers import make_scrollable_canvas

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    help_lbl = QLabel(
        "Custom names used in figure legends and axis labels only. "
        "Leave blank to use the well token (e.g. A01).",
        parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    help_lbl.setAlignment(Qt.AlignLeft)
    layout.addWidget(help_lbl)

    sa, inner = make_scrollable_canvas(parent)
    layout.addWidget(sa, 1)
    app._lbl_canvas = sa
    app._lbl_inner = inner


def label_panel_refresh(app) -> None:
    """Rebuild the well-label entry rows."""
    if not hasattr(app, "_lbl_inner"):
        return

    inner = app._lbl_inner
    inner_layout = inner.layout()
    if inner_layout is None:
        inner_layout = QVBoxLayout(inner)
        inner.setLayout(inner_layout)
    # clear existing rows
    while inner_layout.count():
        item = inner_layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()

    wells = sorted(app._app_state.dataset.well_paths.keys(), key=lambda l: app._parse_rc(l))
    if not wells:
        empty = QLabel("No wells loaded yet.", inner)
        empty.setObjectName("Muted")
        empty.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        inner_layout.addWidget(empty)
        inner_layout.addStretch(1)
        return

    # Header row
    hdr_row = QWidget(inner)
    hr_l = QHBoxLayout(hdr_row)
    hr_l.setContentsMargins(6, 4, 6, 2)
    well_hdr = QLabel("Well", hdr_row)
    well_hdr.setProperty("role", "section")
    well_hdr.setFixedWidth(60)
    hr_l.addWidget(well_hdr)
    lbl_hdr = QLabel("Display label (blank = use well token)", hdr_row)
    lbl_hdr.setProperty("role", "section")
    hr_l.addWidget(lbl_hdr, 1)

    prefix_btn = btn_secondary(hdr_row, "Add Prefix", app._labels_add_prefix)
    prefix_btn.setToolTip(
        "Prepend text to the custom label of every well selected in the "
        "sidebar well picker."
    )
    hr_l.addWidget(prefix_btn)

    suffix_btn = btn_secondary(hdr_row, "Add Suffix", app._labels_add_suffix)
    suffix_btn.setToolTip(
        "Append text to the custom label of every well selected in the "
        "sidebar well picker."
    )
    hr_l.addWidget(suffix_btn)

    inner_layout.addWidget(hdr_row)

    sep = QFrame(inner)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    inner_layout.addWidget(sep)

    for lbl in wells:
        row = QWidget(inner)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(6, 1, 6, 1)

        tok_lbl = QLabel(lbl, row)
        tok_lbl.setFixedWidth(60)
        row_l.addWidget(tok_lbl)

        entry = QLineEdit(row)
        entry.setText(app._app_state.dataset.well_labels.get(lbl, ""))
        row_l.addWidget(entry, 1)

        def _on_change(text: str, t=lbl):
            val = (text or "").strip()
            if val:
                app._app_state.dataset.well_labels[t] = val
            else:
                app._app_state.dataset.well_labels.pop(t, None)
            if hasattr(app, "_invalidate_stats_cache"):
                app._invalidate_stats_cache()
            p = getattr(app, "_persistence", None)
            if p is not None:
                p.schedule_save("sample_definitions")

        entry.textChanged.connect(_on_change)
        inner_layout.addWidget(row)

    inner_layout.addStretch(1)
