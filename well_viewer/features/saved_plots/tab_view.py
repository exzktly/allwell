"""Saved Plots gallery tab — a scrollable list of saved-plot cards.

Each card shows the captured panel image at full tab width, its (renameable)
title + originating-tab badge + timestamp, a collapsible parameter summary
(hidden by default), and Restore / Export / Rename / Delete actions.

The empty-state is driven by the saved-plot count (not dataset presence), so a
freshly-saved plot shows immediately on first open of the tab.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import clear_layout
from well_viewer.features.saved_plots import controller as C
from well_viewer.features.saved_plots.state import get_store


class _AspectImage(QLabel):
    """A QLabel that scales its source pixmap to fill the available width while
    preserving aspect ratio (so a saved figure reads like the real plot)."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self._src = pixmap
        self.setAlignment(Qt.AlignCenter)
        # Expand horizontally; height is driven by the scaled pixmap.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(1)
        self._apply()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._apply()
        super().resizeEvent(event)

    def _apply(self) -> None:
        if self._src.isNull():
            return
        w = max(1, self.width())
        scaled = self._src.scaledToWidth(w, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        if self.height() != scaled.height():
            self.setFixedHeight(scaled.height())


def build_saved_plots_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout() or QVBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)

    # Controls row
    ctrl = QWidget(parent)
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)
    ctx.app._saved_plots_count_lbl = QLabel("", ctrl)
    cl.addWidget(ctx.app._saved_plots_count_lbl)
    cl.addStretch(1)
    clear_btn = QPushButton("Clear all", ctrl)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda: _confirm_clear(ctx.app))
    cl.addWidget(clear_btn)
    layout.addWidget(ctrl)

    # Empty-state ↔ gallery stack, toggled by the saved-plot count (NOT by
    # dataset presence) so a just-saved plot appears on first open.
    from widgets.empty_state import EmptyState
    stack = QStackedWidget(parent)
    stack.addWidget(EmptyState(
        text="No saved plots yet.", icon="image",
        hint="On any plot, hover a panel's top-right corner and click Save.",
    ))  # index 0

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    list_host = QWidget()
    col = QVBoxLayout(list_host)
    col.setContentsMargins(10, 10, 10, 10)
    col.setSpacing(12)
    col.setAlignment(Qt.AlignTop)
    scroll.setWidget(list_host)
    stack.addWidget(scroll)  # index 1

    ctx.app._saved_plots_stack = stack
    ctx.app._saved_plots_list = col
    ctx.app._saved_plots_list_host = list_host
    # Controller repaints the gallery through this callback (avoids a
    # controller → tab_view import cycle).
    ctx.app._saved_plots_repopulate = lambda: populate_saved_plots_grid(ctx.app)

    layout.addWidget(stack, 1)
    populate_saved_plots_grid(ctx.app)


def _confirm_clear(app) -> None:
    try:
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(
            None, "Clear saved plots",
            "Remove all saved plots for this dataset?",
        ) != QMessageBox.Yes:
            return
    except Exception:
        swallowed()  # no dialog (e.g. headless) — fall through to clearing
    C.clear_saved_plots(app)


def populate_saved_plots_grid(app) -> None:
    """Rebuild the gallery from the current store contents and flip the
    empty-state based on how many saved plots exist."""
    col = getattr(app, "_saved_plots_list", None)
    if col is None:
        return
    clear_layout(col)
    store = get_store(app)
    store.ensure_loaded(app)
    records = list(reversed(store.records))  # newest first

    lbl = getattr(app, "_saved_plots_count_lbl", None)
    if lbl is not None:
        n = len(records)
        lbl.setText(f"{n} saved plot{'' if n == 1 else 's'}")

    stack = getattr(app, "_saved_plots_stack", None)
    if stack is not None:
        stack.setCurrentIndex(1 if records else 0)

    for rec in records:
        col.addWidget(_build_saved_card(app, store, rec))


def _build_saved_card(app, store, rec) -> QWidget:
    card = QFrame()
    card.setObjectName("Panel")
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    v = QVBoxLayout(card)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(8)

    # Header: title + tab badge + timestamp
    header = QHBoxLayout()
    title = QLabel(rec.title or rec.tab, card)
    title.setObjectName("PlotCardTitle")
    header.addWidget(title)
    header.addStretch(1)
    badge = QLabel(rec.tab, card)
    badge.setObjectName("Muted")
    header.addWidget(badge)
    v.addLayout(header)

    # Full-width figure (scales with the tab).
    img_path = store.image_path(app, rec)
    pm = QPixmap(str(img_path)) if (img_path is not None and img_path.exists()) else QPixmap()
    if not pm.isNull():
        v.addWidget(_AspectImage(pm, card))
    else:
        missing = QLabel("(image unavailable)", card)
        missing.setObjectName("Muted")
        missing.setAlignment(Qt.AlignCenter)
        v.addWidget(missing)

    ts = QLabel(rec.created_at.replace("T", "  "), card)
    ts.setObjectName("Muted")
    v.addWidget(ts)

    # Parameter summary — collapsed by default, folds out on request.
    if rec.summary:
        from widgets.collapsible_section import CollapsibleSection
        details = CollapsibleSection("Parameters", expanded=False)
        summary = QLabel(rec.summary, card)
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.addWidget(summary)
        v.addWidget(details)

    # Actions
    actions = QHBoxLayout()
    restore_btn = QPushButton("Restore settings", card)
    restore_btn.setToolTip("Re-apply these parameters and open the source tab.")
    restore_btn.clicked.connect(lambda _=False, _id=rec.id: C.restore_saved_plot(app, _id))
    actions.addWidget(restore_btn)
    rename_btn = QPushButton("Rename", card)
    rename_btn.setProperty("variant", "secondary")
    rename_btn.clicked.connect(lambda _=False, _id=rec.id, _t=rec.title: _rename(app, _id, _t))
    actions.addWidget(rename_btn)
    export_btn = QPushButton("Export…", card)
    export_btn.setProperty("variant", "secondary")
    export_btn.clicked.connect(lambda _=False, _id=rec.id: C.export_saved_plot(app, _id))
    actions.addWidget(export_btn)
    actions.addStretch(1)
    del_btn = QPushButton("Delete", card)
    del_btn.setProperty("variant", "secondary")
    del_btn.clicked.connect(lambda _=False, _id=rec.id: C.delete_saved_plot(app, _id))
    actions.addWidget(del_btn)
    v.addLayout(actions)

    return card


def _rename(app, plot_id: str, current: str) -> None:
    try:
        new_name, ok = QInputDialog.getText(
            None, "Rename saved plot", "Name:", text=current or "")
    except Exception:
        return
    if ok and new_name.strip():
        C.rename_saved_plot(app, plot_id, new_name.strip())
