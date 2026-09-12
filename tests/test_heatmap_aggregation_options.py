"""Which heat-map aggregations each Property may ask for.

"Mean above threshold" and "Fraction above threshold" need a cutoff the active
Property is actually measured against. The gate used to be an equality against
mean intensity, on the premise that the only per-channel cutoff was a
brightness — so picking smFISH Count greyed both out and silently swapped the
combo to "Mean (all cells)".

That premise stopped being true when count metrics got ThreshCountOn. The heat
map's own arithmetic already resolved the right cutoff through
``on_cutoff_for_column``; only the dropdown refused, which left the correct
computation sitting behind a control the user could not reach, while Bar, Line
and Scatter answered the same question happily.

Total / Max / Min / Std must still be refused: ThreshFracOn is a *mean*
intensity, and cutting a total (area × mean, orders of magnitude larger) with it
is the same units error relocated. So this pins both directions.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QComboBox

from review.core.channel_state_controller import refresh_heatmap_aggregation_options
from review.features.heatmap.controller import (
    METRIC_COUNT, METRIC_FRACTION, METRIC_MEAN, METRIC_MEAN_ALL,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _combo(qapp, selected: str) -> QComboBox:
    """A metric combo carrying the real option list, on *selected*.

    A plain ``QComboBox`` model has no per-item enable/disable, so the real one
    is backed by a ``QStandardItemModel`` — build the same here or the code
    under test silently no-ops on ``model.item(idx)``.
    """
    cb = QComboBox()
    model = QStandardItemModel(cb)
    for label in (METRIC_MEAN, METRIC_MEAN_ALL, METRIC_FRACTION, METRIC_COUNT):
        model.appendRow(QStandardItem(label))
    cb.setModel(model)
    cb.setCurrentIndex(cb.findText(selected))
    return cb


def _ctx(cb, metric: str):
    return SimpleNamespace(
        state=SimpleNamespace(channel=SimpleNamespace(active_metric=metric)),
        widgets=SimpleNamespace(get_or_none=lambda name: cb),
    )


def _enabled(cb, label: str) -> bool:
    return cb.model().item(cb.findText(label)).isEnabled()


def test_smfish_counts_may_be_thresholded(qapp):
    """The reported gap: counts have ThreshCountOn, so both are available."""
    cb = _combo(qapp, METRIC_FRACTION)
    refresh_heatmap_aggregation_options(_ctx(cb, "smfish_count"))
    assert _enabled(cb, METRIC_MEAN) is True
    assert _enabled(cb, METRIC_FRACTION) is True
    assert cb.currentText() == METRIC_FRACTION, (
        "a selected fraction aggregation was swapped away from a Property that "
        "does have a cutoff"
    )


def test_mean_intensity_may_be_thresholded(qapp):
    cb = _combo(qapp, METRIC_MEAN)
    refresh_heatmap_aggregation_options(_ctx(cb, "mean_intensity"))
    assert _enabled(cb, METRIC_MEAN) is True
    assert _enabled(cb, METRIC_FRACTION) is True
    assert cb.currentText() == METRIC_MEAN


@pytest.mark.parametrize("metric", [
    "total_intensity", "max_intensity", "min_intensity", "std_intensity",
])
def test_the_other_intensity_flavours_are_still_refused(qapp, metric):
    """The counter-test. ThreshFracOn is a mean; these are not measured on it."""
    cb = _combo(qapp, METRIC_FRACTION)
    refresh_heatmap_aggregation_options(_ctx(cb, metric))
    assert _enabled(cb, METRIC_MEAN) is False
    assert _enabled(cb, METRIC_FRACTION) is False
    assert cb.currentText() == METRIC_MEAN_ALL, (
        "a now-unavailable aggregation stayed selected, so the heat map would "
        "draw a threshold it cannot apply"
    )


def test_the_untouched_options_stay_enabled(qapp):
    """Only the two threshold aggregations are ever gated."""
    cb = _combo(qapp, METRIC_MEAN_ALL)
    refresh_heatmap_aggregation_options(_ctx(cb, "total_intensity"))
    assert _enabled(cb, METRIC_MEAN_ALL) is True
    assert _enabled(cb, METRIC_COUNT) is True
