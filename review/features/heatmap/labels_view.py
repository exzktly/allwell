"""The Heat Map's row and column names: the **Labels** page of the Display tab.

A tick label on the heat map can be renamed by double-clicking it. A double
click is a shortcut nobody finds by looking, and phone layout has none, so the
same rename is a row of the Display tab: **Labels** opens a page with one field
per row and per column of the grid, and a field left empty puts the grid's own
label back. Both routes end in :func:`rename_label`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from review.core import inspector_panels
from review.core.ui_helpers import clear_layout
from review.features.heatmap.layout_view import (
    _ensure_sidebar_layout, _persist_and_redraw, refresh_heatmap_layout_panel,
)

#: The Display row's title, and its page's.
LABELS_TITLE = "Labels"


def _layout(ctx):
    return _ensure_sidebar_layout(ctx)


def label_text(ctx, kind: str, idx: int) -> str:
    """The name tick *idx* of that axis carries now (its number when unnamed)."""
    layout = _layout(ctx)
    labels = list((layout.row_labels if kind == "row" else layout.col_labels) or [])
    return labels[idx] if 0 <= idx < len(labels) else str(idx + 1)


def rename_label(ctx, kind: str, idx: int, text: str) -> None:
    """Name row or column *idx* *text*; an empty *text* restores its number.

    Records that the name is the user's, which is what makes it outrank the one
    the sample map would compose for the same tick. Without it the rename is a
    control that runs, writes, redraws, and shows the user something else — and
    on an annotated plate that is every rename. An emptied name clears the mark
    along with the name: the tick is back to the grid's own label, which the
    sample map may speak for again.
    """
    layout = _layout(ctx)
    text = str(text).strip()
    count = layout.rows if kind == "row" else layout.cols
    if not 0 <= idx < count:
        return
    labels = list((layout.row_labels if kind == "row" else layout.col_labels) or [])
    while len(labels) < count:                  # pad a list shorter than the grid
        labels.append(str(len(labels) + 1))
    labels[idx] = text or str(idx + 1)
    if kind == "row":
        layout.row_labels = labels
    else:
        layout.col_labels = labels
    layout.mark_label_typed(kind, idx, bool(text))
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_panel(ctx)
    button = ctx.widgets.get_or_none("_heatmap_labels_button")
    if button is not None:
        inspector_panels.refresh(button)


def summary(ctx) -> str:
    """``3 named`` when the user has named ticks, else nothing."""
    layout = _layout(ctx)
    named = len(layout.row_labels_typed or []) + len(layout.col_labels_typed or [])
    return f"{named} named" if named else ""


def _fill(ctx, form_host: QWidget) -> None:
    """One field per row and per column, read from the grid as it stands."""
    outer = form_host.layout()
    clear_layout(outer)
    layout = _layout(ctx)
    for kind, heading, count in (("row", "Rows", layout.rows), ("col", "Columns", layout.cols)):
        head = QLabel(heading, form_host)
        head.setObjectName("Secondary")
        outer.addWidget(head)
        block = QWidget(form_host)
        form = QFormLayout(block)
        form.setContentsMargins(0, 0, 0, 0)
        typed = set((layout.row_labels_typed if kind == "row" else layout.col_labels_typed) or [])
        for idx in range(count):
            edit = QLineEdit(block)
            edit.setObjectName(f"HeatmapLabel_{kind}_{idx}")
            edit.setPlaceholderText(str(idx + 1))
            # An unnamed tick shows as an empty field over its number, so what
            # the user typed and what the grid supplies can be told apart.
            current = label_text(ctx, kind, idx)
            edit.setText(current if idx in typed or current != str(idx + 1) else "")
            edit.editingFinished.connect(
                lambda k=kind, i=idx, e=edit: _commit(ctx, k, i, e))
            form.addRow(str(idx + 1), edit)
        outer.addWidget(block)
    outer.addStretch(1)


def _commit(ctx, kind: str, idx: int, edit: QLineEdit) -> None:
    text = edit.text().strip()
    if (text or str(idx + 1)) == label_text(ctx, kind, idx):
        return                                   # focus left a field nobody changed
    rename_label(ctx, kind, idx, text)


def build_labels_row(ctx, parent: QWidget) -> QWidget:
    """The Display tab's **Labels** row; pressing it shows the page of fields."""
    button, content, content_layout = inspector_panels.make_popover_button(
        LABELS_TITLE, parent, ctx=ctx, tab="display", summary=lambda: summary(ctx))
    button.setToolTip("Name the heat map's rows and columns")
    note = QLabel(
        "Name a row or a column; leave a field empty for the grid's own label. "
        "Double-clicking a label on the heat map renames it too.", content)
    note.setObjectName("Muted")
    note.setWordWrap(True)
    content_layout.addWidget(note)
    host = QWidget(content)
    QVBoxLayout(host).setContentsMargins(0, 0, 0, 0)
    content_layout.addWidget(host)
    inspector_panels.on_open(button, lambda: _fill(ctx, host))
    ctx.widgets.register("_heatmap_labels_button", button)
    ctx.widgets.register("_heatmap_labels_form", host)
    return inspector_panels.as_display_row(button)
