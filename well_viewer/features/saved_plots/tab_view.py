"""Saved Plots gallery tab — a scrollable grid of saved-plot thumbnails.

Each card shows the captured panel image, its title/timestamp + originating-tab
badge, and the parameter summary, with Restore / Export / Delete actions. The
grid layout mirrors the Cell Browser gallery
(``features/image_browser/cell_browser/tab_view.py``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import clear_layout, wrap_with_empty_state
from well_viewer.features.saved_plots import controller as C
from well_viewer.features.saved_plots.state import get_store

_THUMB_W = 320
_COLS = 2


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

    # Scrollable thumbnail grid
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    grid_host = QWidget()
    grid = QGridLayout(grid_host)
    grid.setContentsMargins(10, 10, 10, 10)
    grid.setSpacing(10)
    grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    scroll.setWidget(grid_host)

    ctx.app._saved_plots_grid = grid
    ctx.app._saved_plots_grid_host = grid_host
    # Controller repaints the gallery through this callback (avoids a
    # controller → tab_view import cycle).
    ctx.app._saved_plots_repopulate = lambda: populate_saved_plots_grid(ctx.app)

    layout.addWidget(wrap_with_empty_state(
        ctx.app, scroll, icon="image",
        text="No saved plots yet.",
        hint="On any plot, hover a panel's top-right corner and click Save.",
    ), 1)

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
    """Rebuild the gallery grid from the current store contents."""
    grid = getattr(app, "_saved_plots_grid", None)
    if grid is None:
        return
    clear_layout(grid)
    store = get_store(app)
    store.ensure_loaded(app)
    records = list(reversed(store.records))  # newest first

    lbl = getattr(app, "_saved_plots_count_lbl", None)
    if lbl is not None:
        n = len(records)
        lbl.setText(f"{n} saved plot{'' if n == 1 else 's'}")

    for i, rec in enumerate(records):
        card = _build_saved_card(app, store, rec)
        grid.addWidget(card, i // _COLS, i % _COLS)


def _build_saved_card(app, store, rec) -> QWidget:
    card = QFrame()
    card.setObjectName("Panel")
    card.setAttribute(Qt.WA_StyledBackground, True)
    v = QVBoxLayout(card)
    v.setContentsMargins(10, 10, 10, 10)
    v.setSpacing(6)

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

    # Thumbnail
    thumb = QLabel(card)
    thumb.setAlignment(Qt.AlignCenter)
    img_path = store.image_path(app, rec)
    if img_path is not None and img_path.exists():
        pm = QPixmap(str(img_path))
        if not pm.isNull():
            thumb.setPixmap(pm.scaledToWidth(
                _THUMB_W, Qt.SmoothTransformation))
    if thumb.pixmap() is None or thumb.pixmap().isNull():
        thumb.setText("(image unavailable)")
        thumb.setObjectName("Muted")
    v.addWidget(thumb)

    ts = QLabel(rec.created_at.replace("T", "  "), card)
    ts.setObjectName("Muted")
    v.addWidget(ts)

    # Parameter summary
    summary = QLabel(rec.summary, card)
    summary.setObjectName("Muted")
    summary.setWordWrap(True)
    summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
    v.addWidget(summary)

    # Actions
    actions = QHBoxLayout()
    restore_btn = QPushButton("Restore settings", card)
    restore_btn.setToolTip("Re-apply these parameters and open the source tab.")
    restore_btn.clicked.connect(lambda _=False, _id=rec.id: C.restore_saved_plot(app, _id))
    actions.addWidget(restore_btn)
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
