"""Feature-column multi-select for the Multivariate tab.

The **Features** page of the inspector: the application's one multi-select
(``widgets.multi_select.MultiSelect``), its rows captioned by column family
(Intensity / Shape / Ratios) and **populated dynamically from the loaded
dataset** — never a hardcoded catalog. The available columns come from
``collect.available_feature_columns`` (metric registry x detected channels,
plus ``detect_shape_features`` for the shape morphology columns actually
emitted by this run, plus active ratios), so a dataset that computed a
different set of shape properties surfaces exactly those.

``build_feature_picker`` fills the page and stashes a ``refresh`` closure on
``ctx.runtime.multivariate.refresh_feature_picker`` so the controller can
rebuild it on activate / dataset-load / channel-change.
"""

from __future__ import annotations

from review.features.multivariate.collect import (
    available_feature_columns, column_display_label,
)
from review.features.multivariate.controller import mv_set_features
from widgets.multi_select import Heading, MultiSelect, summarise


def build_feature_picker(ctx, panel_layout) -> MultiSelect:
    """Build the feature list into *panel_layout* (the Features page's body)."""
    picker = MultiSelect(empty_text="No feature columns in this dataset.")
    picker.setObjectName("MvFeatureList")
    panel_layout.addWidget(picker)
    ctx.widgets.register("_mv_feature_list", picker)
    # Applies as a box is ticked; All and None are one change each.
    picker.changed.connect(lambda cols: _chosen(ctx, cols))

    def refresh() -> None:
        st = ctx.state.feature_slice("multivariate")
        if st is None:
            return
        families = available_feature_columns(ctx)
        items: list = []
        present_cols = []
        for family, cols in families.items():
            items.append(Heading(family))
            for col in cols:
                present_cols.append(col)
                items.append((col, column_display_label(ctx, col)))
        # Drop persisted selections for columns absent in this dataset.
        kept = [c for c in st.feature_cols if c in present_cols]
        if kept != st.feature_cols:
            st.feature_cols = kept
        picker.setItems(items, kept)          # filling the page is not the user choosing

    ctx.runtime.multivariate.refresh_feature_picker = refresh
    refresh()
    return picker


def _chosen(ctx, cols) -> None:
    mv_set_features(ctx, list(cols))
    button = ctx.widgets.get_or_none("_mv_features_btn")
    if button is not None:
        from review.core import inspector_panels
        inspector_panels.refresh(button)


def features_summary(ctx) -> str:
    """``3 of 12`` — what the band's Features row reads."""
    st = ctx.state.feature_slice("multivariate")
    if st is None:
        return ""
    total = sum(len(cols) for cols in available_feature_columns(ctx).values())
    return summarise(len(st.feature_cols), total)
