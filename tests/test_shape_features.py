"""Shape-feature Review wiring (§C): virtual-channel selection + sectioned combo.

Covers the channel-state and UI plumbing that makes ``shape_*`` columns
selectable in the plot channel dropdown under their own section, with the
per-cell metric selector collapsed (a shape feature is one value per cell,
like a ratio). Runs headless (offscreen Qt)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QComboBox

import pipeline.shape_features as sfc
from review.core.channel_state import ChannelState
from review.core.ui_helpers import set_combo_values_sectioned

pytestmark = pytest.mark.qt


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── ChannelState: shape feature as a virtual channel ─────────────────────────

def test_channel_state_shape_feature_roundtrip():
    cs = ChannelState()
    assert cs.is_shape_active() is False

    assert cs.set_active_shape_feature("shape_eccentricity") is True
    assert cs.active_val_col == "shape_eccentricity"
    assert cs.is_shape_active() is True
    # re-selecting the same feature is a no-op
    assert cs.set_active_shape_feature("shape_eccentricity") is False

    # sync_val_col must NOT clobber an active shape feature with <chan>_<metric>
    cs.active_channel = "shape_eccentricity"
    cs.active_metric = "mean_intensity"
    cs.sync_val_col()
    assert cs.active_val_col == "shape_eccentricity"

    # switching to a real channel re-derives the normal intensity column
    assert cs.set_active_channel("gfp") is True
    assert cs.active_val_col == "gfp_mean_intensity"
    assert cs.is_shape_active() is False


# ── set_combo_values_sectioned ───────────────────────────────────────────────

def test_set_combo_values_sectioned(qapp):
    cb = QComboBox()
    sections = [("", ["GFP", "MCHERRY"]), ("Shape Features", ["Eccentricity", "Solidity"])]
    set_combo_values_sectioned(cb, sections)

    texts = [cb.itemText(i) for i in range(cb.count())]
    assert texts == ["GFP", "MCHERRY", "— Shape Features —", "Eccentricity", "Solidity"]

    model = cb.model()
    hdr = texts.index("— Shape Features —")
    assert not model.item(hdr).isEnabled()          # header non-selectable
    assert model.item(texts.index("GFP")).isEnabled()
    assert cb.findText("Eccentricity") == 3         # real values still findable

    # selection preserved across a repopulate
    cb.setCurrentText("Solidity")
    set_combo_values_sectioned(cb, sections)
    assert cb.currentText() == "Solidity"

    # an empty group emits no header
    cb2 = QComboBox()
    set_combo_values_sectioned(cb2, [("", ["GFP"]), ("Shape Features", [])])
    assert [cb2.itemText(i) for i in range(cb2.count())] == ["GFP"]


# ── populate_metric_combo collapses for a shape feature ──────────────────────

class _Chan:
    active_metric = "mean_intensity"
    smfish_channels: set = set()


class _AppState:
    def __init__(self):
        self.channel = _Chan()


class _FakeApp:
    def __init__(self):
        from types import SimpleNamespace
        self._app_state = _AppState()
        # A8/X5: the label→key map is typed state on ChannelState now.
        self._app_state.channel.label_to_channel_key = {
            "GFP": "gfp", "Eccentricity": "shape_eccentricity",
        }
        # active_channel_label reads ctx.state.channel (A8 teardown).
        self._ctx = SimpleNamespace(state=self._app_state, app=self)


def test_populate_metric_combo_collapses_for_shape(qapp):
    from review.core.channel_state_controller import populate_metric_combo
    from review.data.metric_labels import CALCULATED_VAL_LABEL, METRIC_ORDER

    app = _FakeApp()

    # Shape feature → single, inert (disabled) entry.
    cb = QComboBox()
    populate_metric_combo(app._ctx, cb, channel_entry="Eccentricity")
    assert [cb.itemText(i) for i in range(cb.count())] == [CALCULATED_VAL_LABEL]
    assert cb.isEnabled() is False

    # Real channel → full intensity metric set, enabled.
    cb2 = QComboBox()
    populate_metric_combo(app._ctx, cb2, channel_entry="GFP")
    items = [cb2.itemText(i) for i in range(cb2.count())]
    assert items == list(METRIC_ORDER)
    assert cb2.isEnabled() is True


def test_scatter_gate_exempts_shape_columns():
    """The scatter fluor-gate bypass treats shape_* axes like smFISH counts."""
    # mirror the predicate in scatter.controller.build_scatter
    def gate_exempt(col):
        return isinstance(col, str) and (col.endswith("_smfish_count") or sfc.is_shape_column(col))

    assert gate_exempt("shape_eccentricity") is True
    assert gate_exempt("w3cy5_smfish_count") is True
    assert gate_exempt("gfp_mean_intensity") is False


# ── Analyze: compact shape-metric dropdown (replaces the wide chip grid) ──────

def test_checkable_dropdown_data_contract(qapp):
    """The CheckableDropdown is a drop-in for the multi-select chip group:
    addItem / checkedData / setCheckedData / All / None, with a summary."""
    from widgets.checkable_dropdown import CheckableDropdown

    d = CheckableDropdown(noun="metrics selected", empty_text="No metrics selected")
    for label, key in (("Area", "area"), ("Eccentricity", "eccentricity"),
                       ("Solidity", "solidity")):
        d.addItem(label, data=key)
    assert d.count() == 3
    assert d.checkedData() == [] and d._button.text().startswith("No metrics")

    d.setCheckedData(["area", "solidity"])
    assert d.checkedData() == ["area", "solidity"]          # catalog order preserved
    assert d.checkedLabels() == ["Area", "Solidity"]
    assert "2 of 3" in d._button.text()

    d._set_all(True)
    assert d.checkedData() == ["area", "eccentricity", "solidity"]
    assert "All 3" in d._button.text()
    d._set_all(False)
    assert d.checkedData() == []


def test_analyze_shape_selector_is_dropdown(qapp):
    """Regression: the Analyze 'Shape Metrics' selector is the compact dropdown
    (not a wide chip row), defaults round-trip into the run options, and a
    changed selection flows through unchanged."""
    from analyze.tab import AnalyzeTab
    from widgets.checkable_dropdown import CheckableDropdown

    tab = AnalyzeTab()
    sel = tab._shape_chips
    assert isinstance(sel, CheckableDropdown)
    assert sel.count() == len(sfc.SELECTABLE_KEYS)
    # Defaults match the tab's declared default set (order-insensitive).
    assert set(sel.checkedData()) == set(tab._SHAPE_DEFAULTS)

    sel.setCheckedData(["eccentricity", "solidity"])
    assert tab._collect_run_options()["shape_features"] == ["eccentricity", "solidity"]
    # width is capped (was stretching to the full sidebar, looking oversized)
    assert sel.maximumWidth() <= 230


# ── Full-app regressions: shape features survive sub-tab switch + reach scatter ─


def _plot_combo_labels(app):
    cb = app._plotting_channel_cb
    return [cb.itemText(i) for i in range(cb.count())]


def test_shape_section_survives_plot_subtab_switch(loaded_app_shape):
    """Regression: switching plot sub-tabs used to rebuild the global channel
    combo with fluor+ratio only, dropping the Shape Features section so shape
    metrics vanished until the user "wiggled" the channel dropdown."""
    app = loaded_app_shape
    assert any("Eccentricity" in l for l in _plot_combo_labels(app))

    nb = app._plotting_notebook
    nb.setCurrentByName("Bar Plots")
    nb.setCurrentByName("Line Graphs")

    labels = _plot_combo_labels(app)
    # The section header and at least one shape feature are still present —
    # no channel "wiggle" required.
    assert "— Shape Features —" in labels
    assert any("Eccentricity" in l for l in labels)


def test_scatter_menus_offer_shape_features(loaded_app_shape):
    """Regression: the per-cell scatter channel dropdowns never listed shape
    features. Both axes now offer them under their own section, and a shape
    selection resolves to the ``shape_*`` column with the Property combo inert."""
    app = loaded_app_shape
    for cb_attr in ("_scatter_ch_x_cb", "_scatter_ch_y_cb"):
        cb = getattr(app, cb_attr)
        texts = [cb.itemText(i) for i in range(cb.count())]
        assert "— Shape Features —" in texts
        assert "Eccentricity" in texts

    xcb = app._scatter_ch_x_cb
    xcb.setCurrentIndex(xcb.findText("Eccentricity"))
    app._refresh_scatter_metric_for_axis("x")
    metric_x = app._scatter_axis_metric_key("x")
    assert app._col_for_scatter_axis(xcb.currentText(), metric_x) == "shape_eccentricity"
    # A shape feature carries no per-cell metric — the Property combo collapses.
    assert app._scatter_metric_x_cb.isEnabled() is False


def test_scatter_draw_with_shape_axis(loaded_app_shape):
    """End-to-end: a shape feature on the X axis plots real per-cell points."""
    from review.features.scatter.controller import (
        update_scatter_menus, redraw_scatter_orchestrate,
    )
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app_shape
    app._set_selected_wells(set(WELLS))
    update_scatter_menus(app._ctx)
    app._scatter_ch_x_cb.setCurrentIndex(app._scatter_ch_x_cb.findText("Eccentricity"))
    # Fluor channels display upper-case now (GFP, not gfp); the column still
    # resolves to gfp_mean_intensity (case-insensitive).
    app._scatter_ch_y_cb.setCurrentIndex(app._scatter_ch_y_cb.findText("GFP"))
    for tp in list(app._ctx.runtime.scatter.tp_selections):
        app._ctx.runtime.scatter.tp_selections[tp] = True

    redraw_scatter_orchestrate(app._ctx)
    cache = app._ctx.runtime.scatter.interaction_cache
    assert cache["col_x"] == "shape_eccentricity"
    assert cache["col_y"] == "gfp_mean_intensity"
    assert len(cache["points"]) > 0
    # Scatter axes default to the log10(value+1) display transform, which
    # annotates the axis label with a "(log₁₀, +1)" suffix.
    from review.features.scatter._layout import log1p_axis_suffix
    assert app._ax_scatter.get_xlabel() == "Eccentricity" + log1p_axis_suffix(True)


def test_scatter_menus_preserve_timepoint_selection(loaded_app_shape):
    """A selection refresh (adding/removing wells) must NOT reset the scatter
    timepoint filter. Available timepoints are dataset-wide, so a well change
    can't change them — update_scatter_menus used to re-check every timepoint,
    silently pulling all timepoints back into the plot whenever a well was added.
    """
    from review.features.scatter.controller import update_scatter_menus
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app_shape
    wells = list(WELLS)
    app._set_selected_wells({wells[0]})
    update_scatter_menus(app._ctx)

    tp = next(iter(app._ctx.runtime.scatter.tp_selections))
    app._ctx.runtime.scatter.tp_selections[tp] = False                 # user unchecks one tp
    agg_tp = next(iter(app._ctx.runtime.scatter.agg_tp_selections))
    app._ctx.runtime.scatter.agg_tp_selections[agg_tp] = False

    # Add another well → selection_controller fires update_scatter_menus again.
    app._set_selected_wells(set(wells))
    update_scatter_menus(app._ctx)

    assert app._ctx.runtime.scatter.tp_selections[tp] is False         # stays unchecked
    assert app._ctx.runtime.scatter.agg_tp_selections[agg_tp] is False


# ── End-to-end: label → key → active state → value resolution ────────────────

def test_label_key_value_roundtrip():
    from review.core.channel_state_controller import (
        channel_key_for_label, active_channel_label,
    )
    from review.data.data_loading import resolve_value_series
    pd = pytest.importorskip("pandas")

    app = _FakeApp()
    app._app_state.channel = ChannelState()
    # X5: the label→key map is typed state (as update_channel_selector builds it).
    app._app_state.channel.label_to_channel_key = {
        "GFP": "gfp", "Eccentricity": "shape_eccentricity",
    }

    # label → key (as update_channel_selector's map would resolve it)
    key = channel_key_for_label(app._ctx, "Eccentricity")
    assert key == "shape_eccentricity"

    # activating it via the state mutator, then label round-trips back
    assert app._app_state.channel.set_active_shape_feature(key) is True
    assert active_channel_label(app._ctx) == "Eccentricity"

    # the value column resolves directly from the DataFrame (no ratio math)
    df = pd.DataFrame({"shape_eccentricity": [0.1, 0.9, float("nan")]})
    series = resolve_value_series(df, key)
    assert list(series.fillna(-1)) == [0.1, 0.9, -1]


def test_circularity_is_always_emitted_not_selectable():
    """Circularity is always collected (QC gate depends on it), so it's in the
    emitted columns even with no selection, and absent from the Analyze picker."""
    assert "circularity" in sfc.ALWAYS_ON_KEYS
    assert "circularity" not in sfc.SELECTABLE_KEYS
    assert "shape_circularity" in sfc.ALL_COLUMNS
    assert "shape_circularity" in sfc.emitted_columns([])      # no selection
    # Its area+perimeter inputs are always measured.
    props = sfc.regionprops_properties([])
    assert "area" in props and "perimeter" in props
