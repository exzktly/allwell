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

import shape_features_core as sfc
from well_viewer.core.channel_state import ChannelState
from well_viewer.core.ui_helpers import set_combo_values_sectioned


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
        self._app_state = _AppState()
        self._label_to_channel_key = {
            "GFP": "gfp", "Eccentricity": "shape_eccentricity",
        }


def test_populate_metric_combo_collapses_for_shape(qapp):
    from well_viewer.core.channel_state_controller import populate_metric_combo
    from well_viewer.data.metric_labels import CALCULATED_VAL_LABEL, METRIC_ORDER

    app = _FakeApp()

    # Shape feature → single, inert (disabled) entry.
    cb = QComboBox()
    populate_metric_combo(app, cb, channel_entry="Eccentricity")
    assert [cb.itemText(i) for i in range(cb.count())] == [CALCULATED_VAL_LABEL]
    assert cb.isEnabled() is False

    # Real channel → full intensity metric set, enabled.
    cb2 = QComboBox()
    populate_metric_combo(app, cb2, channel_entry="GFP")
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


# ── End-to-end: label → key → active state → value resolution ────────────────

def test_label_key_value_roundtrip():
    from well_viewer.core.channel_state_controller import (
        channel_key_for_label, active_channel_label,
    )
    from well_viewer.data.data_loading import resolve_value_series
    pd = pytest.importorskip("pandas")

    app = _FakeApp()
    app._app_state.channel = ChannelState()

    # label → key (as update_channel_selector's map would resolve it)
    key = channel_key_for_label(app, "Eccentricity")
    assert key == "shape_eccentricity"

    # activating it via the state mutator, then label round-trips back
    assert app._app_state.channel.set_active_shape_feature(key) is True
    assert active_channel_label(app) == "Eccentricity"

    # the value column resolves directly from the DataFrame (no ratio math)
    df = pd.DataFrame({"shape_eccentricity": [0.1, 0.9, float("nan")]})
    series = resolve_value_series(df, key)
    assert list(series.fillna(-1)) == [0.1, 0.9, -1]
