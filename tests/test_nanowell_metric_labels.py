"""The Nanowells page's two fluorescence metrics say on every axis whether the background was subtracted.

Cell brightness is the mean of each counted cell's raw intensity, background included; Nanowell fluorescence is the
nanowell's interior sum minus the tile's floor level. A user could not tell from the old labels which of the two had
the background taken out, and a two-row grid of panels cut each label after two lines, which dropped the end of it:
the very words that tell the two apart, and the channel.
"""
from __future__ import annotations

import pandas as pd
import pytest

from review.features.nanowell import renderer
from review.features.nanowell.distribution_compute import METRICS


def test_each_fluorescence_metric_names_what_it_did_with_the_background():
    assert METRICS["brightness"][0] == "Cell brightness (mean per cell, background included)"
    assert METRICS["brightness_fc"][0] == "Cell brightness fold change (background included)"
    assert METRICS["signal"][0] == "Nanowell fluorescence (total, background subtracted)"
    assert METRICS["signal_fc"][0] == "Nanowell fluorescence fold change (background subtracted)"


@pytest.mark.parametrize("metric", ["brightness", "signal", "brightness_fc", "signal_fc"])
def test_a_wrapped_label_on_a_grid_keeps_every_word(metric):
    # The page writes a channel metric's label as "<label> · <channel>" and wraps it on a grid of two or more rows.
    # The old wrap kept two lines of 26 characters, so each of these lost its background clause and its channel.
    label = f"{METRICS[metric][0]} · GFP"
    wrapped = renderer._wrap_label(label, rows=2)
    assert wrapped.replace("\n", " ") == label
    assert len(wrapped.splitlines()) <= 3


def test_a_short_label_or_a_single_row_is_not_wrapped():
    assert renderer._wrap_label("Cell count", rows=3) == "Cell count"
    long = f"{METRICS['signal_fc'][0]} · GFP"
    assert renderer._wrap_label(long, rows=1) == long


def test_the_site_detail_rows_say_with_and_minus_background():
    from matplotlib.figure import Figure

    frame = pd.DataFrame({"timepoint": ["T0", "T1"], "time_h": [0.0, 24.0], "state": ["measured", "measured"],
                          "n": [1, 2], "c": [0.1, 0.2], "brightness_GFP": [800.0, 820.0],
                          "signal_GFP": [1e4, 2e4]})
    fig = Figure()
    renderer.render_site_detail(fig, [(("A1", 0, 0), frame)], channel="GFP")
    labels = [ax.get_ylabel() for ax in fig.axes]
    assert labels == ["Cell count", "Confluence", "Cell brightness\nwith background\nGFP",
                      "Nanowell fluor.\nminus background\nGFP"]
