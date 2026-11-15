"""A threshold's label says what its comparison does.

The Cell Gating table headed its columns "Include ≥" and "ON ≥" and its
tooltip said "at or above this cut", and the binary image view was named
"≥ threshold", while every computation behind them is a strict ``>``. On the
values 49, 50, 50, 51 with a cut of 50 the label promises three cells and the
app includes, and counts ON, one. The comparison is the established behaviour
that every saved threshold was chosen against, so the labels changed:
``domain.gating_state.CUT_SYMBOL`` is the one spelling.

Three parts: the comparison measured at the value where ``>`` and ``≥`` differ;
the labels a user reads, as values; and a scan that fails on any string in the
source pairing ``≥`` or "at or above" with a threshold word — shown biting on
the strings that shipped and clearing the legitimate uses of ``≥``.
"""
from __future__ import annotations

import ast
import re

import pandas as pd
import pytest

from review.data.data_loading import aggregate_with_threshold_df
from review.domain.gating_state import CUT_SYMBOL
from review.features.home.cell_gating.gating_compute import apply_fluor_gating, gate_table_headers
from review.features.saved_cells.collect import intensity_gates_text
from tests._repo_scan import REPO_ROOT, parse_text, parsed_sources, rel

N_ON, N_TOTAL = 4, 5


def _cells() -> pd.DataFrame:
    return pd.DataFrame({"gfp_mean_intensity": [49.0, 50.0, 50.0, 51.0], "area_px": 100.0, "timepoint_hours": 0.0})


# ── the comparison ──────────────────────────────────────────────────────────

def test_a_floor_keeps_the_cells_strictly_above_it():
    df = _cells()
    included = apply_fluor_gating(df, fluor_gates={"gfp": 50.0}, use_scatter=False, gating_gates=[],
                                  use_thresh=False, thresh_gates=[], ratios={})
    assert included == 1 and df["Included"].tolist() == [0, 0, 0, 1]


def test_on_is_strictly_above_the_cutoff():
    point = aggregate_with_threshold_df(_cells(), 50.0, val_col="gfp_mean_intensity")[0]
    assert (int(point[N_ON]), int(point[N_TOTAL])) == (1, 4)


# ── the labels ──────────────────────────────────────────────────────────────

def test_the_symbol_is_the_strict_one():
    assert CUT_SYMBOL == ">"


def test_the_cell_gating_headers_say_strictly_above():
    (include_header, include_tip), = gate_table_headers("include")
    assert include_header == "Include >\n(FluorGating, per-cell MFI)"
    assert "at or below the floor are excluded" in include_tip
    (frac_header, frac_tip), (count_header, count_tip) = gate_table_headers("on")
    assert frac_header == "ON >\n(ThreshFracOn, per-cell MFI)"
    assert count_header == "ON >\n(ThreshCountOn, spot count)"
    assert "cells above this cut count as 'on'" in frac_tip and "at or above" not in frac_tip
    assert "cells above this count as 'on'" in count_tip


def test_saved_cells_names_a_floor_with_the_same_symbol():
    assert intensity_gates_text([("GFP", 120.0), ("MCHERRY", 7.5)]) == (
        "Intensity gates in force: GFP > 120, MCHERRY > 7.5")
    assert intensity_gates_text([]) == "No intensity gate is in force."


# ── the scan ────────────────────────────────────────────────────────────────

_CUT_WORD = r"(?:include|on|thr\w*|cut\w*|floor)"
_INCLUSIVE_CUT = re.compile(
    rf"\b{_CUT_WORD}\s*(?:≥|>=)(?!\w)"          # "Include ≥", "ON ≥"
    rf"|(?:≥|>=)\s*(?:on\b|thr\w*|cutoff)"      # "≥ threshold", "≥thr", "≥ ON threshold"
    rf"|at or above (?:this cut\b|(?:the|its) (?:cutoff|floor|threshold))",
    re.IGNORECASE)


def _inclusive_cut_strings(tree: ast.AST) -> list[str]:
    """Every string in *tree* — labels, tooltips, docstrings, f-string parts — that words a cut as inclusive."""
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _INCLUSIVE_CUT.search(node.value)]


SHIPPED = {
    "the include header": 'h = "Include \\u2265\\n(FluorGating, per-cell MFI)"',
    "the on header": 'h = "ON ≥\\n(ThreshFracOn, per-cell MFI)"',
    "the tooltip": 'tip = "Included cells at or above this cut count as \'on\'"',
    "the render mode": 'modes = [("threshold", "≥ threshold")]',
    "the short render mode": 'short = {"threshold": "≥thr"}',
    "the hint": 'hint = "reaches the channel\'s ON ≥ cutoff."',
    "the binary view": 'tip = "drives the Binary (≥ ON threshold) overlay"',
}
LEGITIMATE = {
    "a nanowell subset": 'label = "Starting cells ≥1 (occupied)"',
    "a share": 'title = "Occupied share (nanowells holding ≥ 1 cell)"',
    "a range check": 'doc = "a min at or above max is a typo"',
    "an optimiser score": 'score = f"separation ≥ {d:.3f} at 95% confidence"',
    "a strict header": 'h = "ON >\\n(ThreshFracOn, per-cell MFI)"',
    # The QC focus minimum is a different cut and is inclusive (``focus_distribution.kept``: ``v >= cut``).
    "the focus minimum": 'doc = "Cells at or above the cut. Every measurable cell without one."',
    "code, not words": "keep = values >= threshold",
}


@pytest.mark.parametrize("shape", sorted(SHIPPED))
def test_the_scan_catches_a_label_that_shipped(shape):
    assert _inclusive_cut_strings(parse_text(SHIPPED[shape])), SHIPPED[shape]


@pytest.mark.parametrize("shape", sorted(LEGITIMATE))
def test_the_scan_clears_a_legitimate_string(shape):
    assert not _inclusive_cut_strings(parse_text(LEGITIMATE[shape])), LEGITIMATE[shape]


def test_no_string_in_the_app_words_a_cut_as_inclusive():
    found = [f"{rel(path)}: {text.strip()[:90]!r}"
             for path, tree in parsed_sources() for text in _inclusive_cut_strings(tree)]
    assert not found, "a cut is strict (>) and is labelled so:\n  " + "\n  ".join(found)


def test_the_manual_names_the_columns_as_the_app_does():
    manual = (REPO_ROOT / "_Docs" / "Markdowns" / "README_WellViewer.md").read_text(encoding="utf-8")
    assert "`Include >`" in manual and "`ON > (ThreshFracOn)`" in manual
    for stale in ("Include ≥", "ON ≥", "≥ threshold"):
        assert stale not in manual, stale
