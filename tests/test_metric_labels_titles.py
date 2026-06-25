"""Title-helper wording after smFISH counts started honouring ThreshFracOn.

smFISH counts used to be plotted with thresholds bypassed, so the titles
special-cased them ("all spots, thresholds ignored" / "with detected spots").
Now counts honour the channel's ThreshFracOn cutoff just like intensities, so
the generic "above threshold" wording applies to every metric.
"""

from __future__ import annotations

import pytest

from well_viewer.data.metric_labels import (
    events_per_fov_title,
    events_total_title,
    fraction_title,
    threshold_qualifier,
)


@pytest.mark.parametrize("metric_key", ["smfish_count", "mean_intensity", "max_intensity"])
def test_titles_are_generic_above_threshold_for_every_metric(metric_key):
    # smFISH counts must read the same as intensities — no "thresholds
    # ignored" / "detected spots" wording anymore.
    assert threshold_qualifier(metric_key) == " (above threshold)"
    assert fraction_title(metric_key) == "Fraction of Cells Above Threshold"
    assert events_total_title(metric_key) == "Events above threshold (N)"
    assert events_per_fov_title(metric_key, "SD") == "Mean events above threshold per FOV ± SD"


def test_smfish_titles_no_longer_claim_thresholds_ignored():
    for text in (
        threshold_qualifier("smfish_count"),
        fraction_title("smfish_count"),
        events_total_title("smfish_count"),
        events_per_fov_title("smfish_count", "SEM"),
    ):
        assert "ignored" not in text.lower()
        assert "detected spots" not in text.lower()
