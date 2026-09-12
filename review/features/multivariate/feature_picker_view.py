"""Feature-column multi-select for the Multivariate tab.

A checkable list, grouped by column family (Intensity / Shape / Ratios), that is
**populated dynamically from the loaded dataset** — never a hardcoded catalog.
The available columns come from ``collect.available_feature_columns`` (metric
registry x detected channels, plus ``detect_shape_features`` for the shape
morphology columns actually emitted by this run, plus active ratios), so a
dataset that computed a different set of shape properties surfaces exactly those.

``build_feature_picker`` fills a popover panel and stashes a ``refresh`` closure
on ``app._mv_refresh_feature_picker`` so the controller can rebuild it on
activate / dataset-load / channel-change.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem

from review.features.multivariate.collect import (
    available_feature_columns, column_display_label,
)
from review.features.multivariate.controller import mv_set_features

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_LISTW_MIN_W = 240
_LISTW_MIN_H = 260


def build_feature_picker(ctx, panel_layout) -> None:
    """Build the checkable feature list into *panel_layout* (a popover body)."""
    panel_layout.addWidget(QLabel("Feature columns:"))
    listw = QListWidget()
    listw.setObjectName("MvFeatureList")
    listw.setMinimumSize(_LISTW_MIN_W, _LISTW_MIN_H)
    listw.setUniformItemSizes(False)
    panel_layout.addWidget(listw)
    # A8 burn-down: widget handle via the WidgetRegistry (stamps app._mv_feature_list).
    ctx.widgets.register("_mv_feature_list", listw)

    def _on_item_changed(_item=None):
        if ctx.runtime.multivariate.picker_refreshing:
            return
        cols = []
        for i in range(listw.count()):
            it = listw.item(i)
            col = it.data(Qt.UserRole)
            if col is not None and it.checkState() == Qt.Checked:
                cols.append(col)
        mv_set_features(ctx, cols)

    listw.itemChanged.connect(_on_item_changed)

    def refresh() -> None:
        st = ctx.state.feature_slice("multivariate")
        if st is None:
            return
        ctx.runtime.multivariate.picker_refreshing = True
        try:
            listw.clear()
            families = available_feature_columns(ctx)
            selected = set(st.feature_cols)
            present_cols = []
            for family, cols in families.items():
                header = QListWidgetItem(family)
                header.setFlags(Qt.NoItemFlags)  # non-selectable divider
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                listw.addItem(header)
                for col in cols:
                    present_cols.append(col)
                    item = QListWidgetItem(f"  {column_display_label(ctx, col)}")
                    item.setData(Qt.UserRole, col)
                    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    item.setCheckState(
                        Qt.Checked if col in selected else Qt.Unchecked)
                    listw.addItem(item)
            # Drop persisted selections for columns absent in this dataset.
            kept = [c for c in st.feature_cols if c in present_cols]
            if kept != st.feature_cols:
                st.feature_cols = kept
        finally:
            ctx.runtime.multivariate.picker_refreshing = False

    ctx.runtime.multivariate.refresh_feature_picker = refresh
    refresh()
