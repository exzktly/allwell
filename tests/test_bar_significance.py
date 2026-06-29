"""Significance-bracket helpers on the Bar plot (N7)."""

from __future__ import annotations

from well_viewer.features.barplot.renderer import _significance_marker


def test_significance_marker_thresholds():
    assert _significance_marker(0.0005) == "***"
    assert _significance_marker(0.005) == "**"
    assert _significance_marker(0.03) == "*"
    assert _significance_marker(0.2) == "ns"
    # Boundary values fall to the less-significant bucket (strict <).
    assert _significance_marker(0.05) == "ns"
    assert _significance_marker(0.01) == "*"
