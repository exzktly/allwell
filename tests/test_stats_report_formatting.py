"""Unit tests for the pure stats-report formatters extracted from run_stats (A5).

``run_stats`` (CC 52) mixed Qt-widget reads, validation, statistics, and a large
block of text formatting that had no direct tests. The formatting is now pure
helpers — this locks their behaviour (and covers logic the god-function never
had a direct test for).
"""
from __future__ import annotations

from well_viewer.features.stats.controller import (
    _build_header_lines,
    _format_group_summary_lines,
    _format_pairwise_lines,
    _resolve_sample_units,
    _sig_marker,
)


def test_sig_marker_thresholds():
    assert _sig_marker(0.0005) == "***"
    assert _sig_marker(0.005) == "**"
    assert _sig_marker(0.03) == "*"
    assert _sig_marker(0.2) == "ns"
    # Boundaries are strict "<".
    assert _sig_marker(0.001) == "**"
    assert _sig_marker(0.05) == "ns"


def test_resolve_sample_units():
    assert _resolve_sample_units(True, []) == "per-cell values"
    assert _resolve_sample_units(True, ["G1"]) == "per-cell values"
    assert "per-well samples" == _resolve_sample_units(False, [])
    assert "single-well" in _resolve_sample_units(False, ["G1"])


def test_group_summary_skips_empty_and_computes_stats():
    lines = _format_group_summary_lines([("A", [1.0, 2.0, 3.0]), ("B", [])])
    # B (empty) is skipped; A summarised; trailing "" always appended.
    assert lines[-1] == ""
    body = [ln for ln in lines if ln]
    assert len(body) == 1
    assert body[0].startswith("A:  n=3")
    assert "mean=2" in body[0]
    assert "median=2" in body[0]
    assert "sd=1" in body[0]


def test_group_summary_single_value_sd_zero():
    lines = _format_group_summary_lines([("Solo", [5.0])])
    body = [ln for ln in lines if ln]
    assert body[0].startswith("Solo:  n=1")
    assert "sd=0" in body[0]


def test_header_lines_ks_variant():
    lines = _build_header_lines(
        test="KS test", channel_lbl="GFP", stat_lbl="mean", threshold=1.5,
        tp_str="24", is_ks=True, solo_groups=[],
    )
    joined = "\n".join(lines)
    assert "Statistic:  per-cell distribution" in joined
    assert "Samples:    per-cell values" in joined
    assert "Threshold:  1.50" in joined


def test_header_lines_solo_note_inserted():
    lines = _build_header_lines(
        test="Mann-Whitney U", channel_lbl="GFP", stat_lbl="mean", threshold=1.0,
        tp_str="24", is_ks=False, solo_groups=["G1", "G2"],
    )
    joined = "\n".join(lines)
    assert "Note:       single-well group(s) G1, G2" in joined
    assert "Statistic:  mean" in joined


def test_pairwise_lines_significant_error_and_adjusted():
    pair_results = [
        ("A", "B", "U", 12.5, 0.0005, None),
        ("A", "C", "U", None, None, "too few samples"),
    ]
    adjusted = {0: 0.001}
    lines = _format_pairwise_lines(pair_results, adjusted, show_adjusted=True)
    joined = "\n".join(lines)
    assert "── A  vs  B ──" in joined
    assert "U = 12.5000" in joined
    assert "***" in joined                     # raw p 0.0005
    assert "p_BH = 0.001" in joined            # adjusted shown
    assert "Error: too few samples" in joined  # error branch
    assert "Benjamini-Hochberg" in joined      # footer when show_adjusted


def test_pairwise_lines_no_adjusted_footer_when_hidden():
    pair_results = [("A", "B", "U", 3.0, 0.2, None)]
    lines = _format_pairwise_lines(pair_results, {}, show_adjusted=False)
    joined = "\n".join(lines)
    assert "Benjamini-Hochberg" not in joined
    assert "p_BH" not in joined
    assert "ns" in joined
