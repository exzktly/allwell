"""The Nanowells page's two fluorescence metrics say on every axis what their background was.

Cell fluorescence (the mean of each counted cell's intensity) and Nanowell fluorescence (the nanowell's interior sum)
are measured above one background, chosen when the run is set up: the well's own nanowell floor, or the empty well's
(``review/features/nanowell/background_compute``). A run from before that choice left the background in its cell
fluorescence. A reader cannot tell any of that from the values, so the label says it; and a two-row grid of panels
once cut each label after two lines, which dropped exactly those words and the channel.
"""
from __future__ import annotations

import pandas as pd
import pytest

from review.features.nanowell import background_compute as bc
from review.features.nanowell import renderer
from review.features.nanowell.distribution_compute import METRICS, metric_label


@pytest.mark.parametrize(("metric", "mode", "label"), [
    ("brightness", bc.FLOOR, "Cell fluorescence (mean per cell, own-well background subtracted) · GFP"),
    ("brightness", bc.EMPTY_WELL, "Cell fluorescence (mean per cell, empty-well background subtracted) · GFP"),
    ("brightness", bc.NONE, "Cell fluorescence (mean per cell, background included) · GFP"),
    ("brightness_fc", bc.NONE, "Cell fluorescence fold change (background included) · GFP"),
    ("signal", bc.EMPTY_WELL, "Nanowell fluorescence (total, empty-well background subtracted) · GFP"),
    # A run from before the choice took the tile's floor off its nanowell fluorescence all the same.
    ("signal", bc.NONE, "Nanowell fluorescence (total, own-well background subtracted) · GFP"),
    ("signal_fc", bc.FLOOR, "Nanowell fluorescence fold change (own-well background subtracted) · GFP"),
])
def test_a_fluorescence_label_names_its_background(metric, mode, label):
    assert metric_label(metric, "GFP", [mode, mode]) == label


def test_wells_measured_differently_say_so_and_other_metrics_say_nothing():
    assert metric_label("brightness", "GFP", [bc.FLOOR, bc.EMPTY_WELL]) == (
        "Cell fluorescence (mean per cell, background differs between wells) · GFP")
    # Both old and new runs took the floor off nanowell fluorescence, so a mix of the two still agrees there.
    assert metric_label("signal", "GFP", [bc.NONE, bc.FLOOR]) == (
        "Nanowell fluorescence (total, own-well background subtracted) · GFP")
    assert metric_label("n", None, [bc.EMPTY_WELL]) == "Cell count"
    assert metric_label("confluence_fc", None, [bc.NONE]) == "Confluence fold change"


def test_the_menu_names_are_the_bare_metrics():
    # The Metric menu is built before any dataset is read, so it names the metric and leaves the background to
    # the axis, which knows it.
    assert METRICS["brightness"][0] == "Cell fluorescence (mean per cell)"
    assert METRICS["signal_fc"][0] == "Nanowell fluorescence fold change"


@pytest.mark.parametrize("metric", ["brightness", "signal", "brightness_fc", "signal_fc"])
def test_a_wrapped_label_on_a_grid_keeps_every_word(metric):
    # The old wrap kept two lines of 26 characters, so each of these lost its background clause and its channel.
    label = metric_label(metric, "GFP", [bc.EMPTY_WELL])
    wrapped = renderer._wrap_label(label, rows=2)
    assert wrapped.replace("\n", " ") == label
    assert len(wrapped.splitlines()) <= 3


def test_a_short_label_or_a_single_row_is_not_wrapped():
    assert renderer._wrap_label("Cell count", rows=3) == "Cell count"
    long = metric_label("signal_fc", "GFP", [bc.EMPTY_WELL])
    assert renderer._wrap_label(long, rows=1) == long


@pytest.mark.parametrize(("modes", "brightness", "signal"), [
    ([bc.EMPTY_WELL], "Cell fluor.\nminus empty well\nGFP", "Nanowell fluor.\nminus empty well\nGFP"),
    ([bc.NONE], "Cell fluor.\nwith background\nGFP", "Nanowell fluor.\nminus own well\nGFP"),
])
def test_the_site_detail_rows_say_what_was_subtracted(modes, brightness, signal):
    from matplotlib.figure import Figure

    frame = pd.DataFrame({"timepoint": ["T0", "T1"], "time_h": [0.0, 24.0], "state": ["measured", "measured"],
                          "n": [1, 2], "c": [0.1, 0.2], "brightness_GFP": [800.0, 820.0],
                          "signal_GFP": [1e4, 2e4]})
    fig = Figure()
    renderer.render_site_detail(fig, [(("A1", 0, 0), frame)], channel="GFP", backgrounds=modes)
    assert [ax.get_ylabel() for ax in fig.axes] == ["Cell count", "Confluence", brightness, signal]
