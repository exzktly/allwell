"""Home → QC Triage: every known problem with the plate, one row each.

Structure only. The rows come from :mod:`.collect` and every button's effect
from :mod:`.controller`; this module arranges them and enables each button only
when at least one selected row supports it, so a control that is offered is a
control that does something (CLAUDE.md §7.6).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui import theme

from . import collect as tc
from . import controller as tcc

#: Column headings, in order.
COLUMNS = ("Stage", "Well", "Field", "Time", "Problem", "Decision")

_STATE_WORDS = {
    tc.STATE_OPEN: "open",
    tc.STATE_ACCEPTED: "accepted",
    tc.STATE_EXCLUDED: "field excluded",
}


def _decision_text(ctx, row) -> str:
    state = tc.row_state(ctx, row)
    words = _STATE_WORDS[state]
    if state == tc.STATE_EXCLUDED:
        meta = tc.excluded_fields(ctx).get(
            tc.field_key(row.well, row.fov, row.timepoint)) or {}
        reason = str(meta.get("reason") or "")
        return f"{words} — {reason}" if reason else words
    if row.exclude_refusal and state == tc.STATE_OPEN:
        return f"{words} — cannot exclude: {row.exclude_refusal}"
    return words


def build_triage_subtab(ctx, parent: QWidget) -> None:
    layout = parent.layout() or QVBoxLayout(parent)
    layout.setSpacing(theme.Spacing.sm)

    summary = QLabel(parent)
    summary.setObjectName("Muted")
    summary.setWordWrap(True)
    layout.addWidget(summary)

    table = QTableWidget(0, len(COLUMNS), parent)
    table.setObjectName("TriageTable")
    table.setHorizontalHeaderLabels(list(COLUMNS))
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(
        COLUMNS.index("Problem"), QHeaderView.Stretch)
    layout.addWidget(table, 1)

    buttons = QHBoxLayout()
    buttons.setSpacing(theme.Spacing.sm)
    open_btn = QPushButton("Open in Segmentation", parent)
    exclude_btn = QPushButton("Exclude field…", parent)
    accept_btn = QPushButton("Accept", parent)
    reopen_btn = QPushButton("Reopen", parent)
    rerun_btn = QPushButton("Re-run wells…", parent)
    for btn, tip in (
        (open_btn, "Show this field in Image Browser → Segmentation."),
        (exclude_btn, "Leave the selected fields out of every plot, statistic "
                      "and export, with a reason. Reversible with Reopen."),
        (accept_btn, "Mark the selected findings as seen and acceptable."),
        (reopen_btn, "Undo Accept, and put excluded fields back."),
        (rerun_btn, "Open Analyze set up to re-segment the selected wells "
                    "from scratch, leaving every other well alone."),
    ):
        btn.setToolTip(tip)
        buttons.addWidget(btn)
    buttons.addStretch(1)
    layout.addLayout(buttons)

    rows_by_index: list = []

    def _selected():
        picked = sorted({i.row() for i in table.selectedIndexes()})
        return [rows_by_index[i] for i in picked if i < len(rows_by_index)]

    def _sync_buttons() -> None:
        sel = _selected()
        states = [tc.row_state(ctx, r) for r in sel]
        open_btn.setEnabled(len(sel) == 1 and bool(sel[0].well))
        exclude_btn.setEnabled(any(r.can_exclude and s != tc.STATE_EXCLUDED
                                   for r, s in zip(sel, states, strict=True)))
        accept_btn.setEnabled(tc.STATE_OPEN in states)
        reopen_btn.setEnabled(any(s != tc.STATE_OPEN for s in states))
        rerun_btn.setEnabled(bool(tc.rerun_wells(sel)))

    def refresh() -> None:
        rows = tc.triage_rows(ctx)
        rows_by_index[:] = rows
        summary.setText(tc.summary(ctx, rows))
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            cells = (row.stage, row.well, row.fov, row.timepoint, row.detail,
                     _decision_text(ctx, row))
            for j, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if j == COLUMNS.index("Problem"):
                    item.setToolTip(str(text))
                table.setItem(i, j, item)
        _sync_buttons()

    def _ask_reason() -> str | None:
        from review.core.ui_helpers import ask_name_dialog
        return ask_name_dialog(ctx.window, title="Exclude field",
                               prompt="Why are these fields being excluded?",
                               default="")

    def _on_exclude() -> None:
        reason = _ask_reason()
        if not reason:
            return
        n = tcc.exclude_fields(ctx, _selected(), reason)
        ctx.notify.set_status(f"Excluded {n} field(s): {reason}.")
        refresh()

    def _on_accept() -> None:
        n = tcc.accept(ctx, _selected())
        ctx.notify.set_status(f"Accepted {n} finding(s).")
        refresh()

    def _on_reopen() -> None:
        n = tcc.reopen(ctx, _selected())
        ctx.notify.set_status(f"Reopened {n} finding(s).")
        refresh()

    def _on_rerun() -> None:
        wells = tcc.rerun_wells(ctx, _selected())
        if wells:
            ctx.notify.set_status(
                f"Analyze is set up to re-run {', '.join(wells)} — press Run there.")

    def _on_open() -> None:
        sel = _selected()
        if sel and not tcc.open_field(ctx, sel[0]):
            ctx.notify.set_status("Image Browser is not part of this build.")

    table.itemSelectionChanged.connect(_sync_buttons)
    open_btn.clicked.connect(_on_open)
    exclude_btn.clicked.connect(_on_exclude)
    accept_btn.clicked.connect(_on_accept)
    reopen_btn.clicked.connect(_on_reopen)
    rerun_btn.clicked.connect(_on_rerun)

    ctx.widgets.register("_triage_table", table)
    ctx.runtime.triage.refresh = refresh
    refresh()
