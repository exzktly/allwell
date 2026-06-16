"""Scatter gates as the Cell-Gating inclusion population.

Covers the three pure seams the feature adds (no Qt): gates are stamped with
their axis columns at draw time, ``compute_gate_inclusion`` turns the union of
gates into a boolean mask, and the persistence block round-trips the gates +
mode flag through ``pipeline_info.json``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from well_viewer.features.scatter.gating import (
    _on_gate_drawn,
    compute_gate_inclusion,
)


# ── gate stamping ─────────────────────────────────────────────────────────────

def test_on_gate_drawn_stamps_axis_columns_from_interaction_cache():
    """A drawn gate records the metric columns of the axes it was drawn on so it
    can be re-applied to a dataframe out of context (Cell Gating inclusion)."""
    app = SimpleNamespace(
        _scatter_gates=[],
        _scatter_interaction_cache={
            "col_x": "gfp_mean_intensity",
            "col_y": "mcherry_mean_intensity",
        },
        _scatter_gate_redraw_cb=lambda _a: None,
    )
    _on_gate_drawn(app, SimpleNamespace(xdata=1.0, ydata=2.0),
                   SimpleNamespace(xdata=5.0, ydata=8.0))

    assert len(app._scatter_gates) == 1
    g = app._scatter_gates[0]
    assert g["x_col"] == "gfp_mean_intensity"
    assert g["y_col"] == "mcherry_mean_intensity"
    assert (g["x0"], g["x1"], g["y0"], g["y1"]) == (1.0, 5.0, 2.0, 8.0)


def test_on_gate_drawn_notifies_both_gate_changed_listeners():
    """Drawing a gate fires *both* the sidebar count callback and the Cell
    Gating summary callback, so the Cell Gating tab's gate summary stays live
    even though the gate was drawn on the Scatter tab."""
    calls: list[str] = []
    app = SimpleNamespace(
        _scatter_gates=[],
        _scatter_gate_count_cb=lambda: calls.append("count"),
        _scatter_gates_changed_cb=lambda: calls.append("summary"),
        _scatter_gate_redraw_cb=lambda _a: None,
    )
    _on_gate_drawn(app, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=1.0, ydata=1.0))
    assert calls == ["count", "summary"]


def test_on_gate_drawn_assigns_default_name():
    """Gates are nameable: with no Qt canvas (headless), each gets the next
    default ``Gate N`` name so the Cell Gating dropdown always has a label."""
    app = SimpleNamespace(_scatter_gates=[], _scatter_gate_redraw_cb=lambda _a: None)
    _on_gate_drawn(app, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=1.0, ydata=1.0))
    _on_gate_drawn(app, SimpleNamespace(xdata=2.0, ydata=2.0),
                   SimpleNamespace(xdata=3.0, ydata=3.0))
    assert [g["name"] for g in app._scatter_gates] == ["Gate 1", "Gate 2"]


def test_selected_scatter_gates_honours_choice():
    """The filtering-gate dropdown choice picks one named gate; None = union."""
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_selected_scatter_gates,
    )
    g1 = {"x0": 0, "y0": 0, "x1": 1, "y1": 1, "name": "Gate 1"}
    g2 = {"x0": 2, "y0": 2, "x1": 3, "y1": 3, "name": "Gate 2"}
    app = SimpleNamespace(_scatter_gates=[g1, g2])

    app._cell_gating_scatter_gate_choice = None          # union
    assert cell_gating_selected_scatter_gates(app) == [g1, g2]
    app._cell_gating_scatter_gate_choice = "Gate 2"       # single gate
    assert cell_gating_selected_scatter_gates(app) == [g2]
    app._cell_gating_scatter_gate_choice = "Gone"         # deleted gate
    assert cell_gating_selected_scatter_gates(app) == []


def test_rename_gate_rejects_blank_and_duplicate_keeps_choice():
    from well_viewer.features.scatter.gating import rename_gate

    g1 = {"x0": 0, "y0": 0, "x1": 1, "y1": 1, "name": "Gate 1"}
    g2 = {"x0": 2, "y0": 2, "x1": 3, "y1": 3, "name": "Gate 2"}
    app = SimpleNamespace(
        _scatter_gates=[g1, g2],
        _cell_gating_scatter_gate_choice="Gate 1",
        _scatter_gate_redraw_cb=lambda _a: None,
    )
    assert rename_gate(app, 0, "") is False           # blank rejected
    assert rename_gate(app, 0, "Gate 2") is False      # duplicate rejected
    assert g1["name"] == "Gate 1"                       # unchanged
    assert rename_gate(app, 0, "Live") is True          # valid rename
    assert g1["name"] == "Live"
    # The filtering choice follows the rename so the selection isn't lost.
    assert app._cell_gating_scatter_gate_choice == "Live"


def test_on_gate_drawn_without_cache_leaves_columns_none():
    """No prior redraw (no interaction cache) ⇒ the gate is still created but its
    columns are None, so the inclusion pass simply skips it."""
    app = SimpleNamespace(_scatter_gates=[], _scatter_gate_redraw_cb=lambda _a: None)
    _on_gate_drawn(app, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=1.0, ydata=1.0))
    g = app._scatter_gates[0]
    assert g["x_col"] is None and g["y_col"] is None


# ── compute_gate_inclusion ────────────────────────────────────────────────────

def _df():
    return pd.DataFrame({
        "gfp_mean_intensity":     [10.0, 100.0, 100.0, 500.0, np.nan],
        "mcherry_mean_intensity": [10.0, 100.0, 800.0, 500.0, 50.0],
    })


def test_inclusion_single_gate_membership():
    df = _df()
    gate = {"x0": 50.0, "x1": 200.0, "y0": 50.0, "y1": 200.0,
            "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}
    mask = compute_gate_inclusion(df, [gate])
    # Only row 1 (100, 100) lies inside the box.
    assert mask.tolist() == [False, True, False, False, False]


def test_inclusion_is_union_of_gates():
    df = _df()
    g1 = {"x0": 50.0, "x1": 200.0, "y0": 50.0, "y1": 200.0,
          "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}
    g2 = {"x0": 400.0, "x1": 600.0, "y0": 400.0, "y1": 600.0,
          "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}
    mask = compute_gate_inclusion(df, [g1, g2])
    # row 1 inside g1, row 3 inside g2 → union.
    assert mask.tolist() == [False, True, False, True, False]


def test_inclusion_nan_rows_excluded():
    df = _df()
    # A wide gate that would cover row 4 if its NaN x compared True.
    gate = {"x0": 0.0, "x1": 1e9, "y0": 0.0, "y1": 1e9,
            "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}
    mask = compute_gate_inclusion(df, [gate])
    assert not mask[4]  # NaN gfp never falls inside


def test_inclusion_gate_missing_columns_contributes_nothing():
    df = _df()
    no_cols = {"x0": 0.0, "x1": 1e9, "y0": 0.0, "y1": 1e9}            # never stamped
    absent = {"x0": 0.0, "x1": 1e9, "y0": 0.0, "y1": 1e9,
              "x_col": "not_a_column", "y_col": "mcherry_mean_intensity"}
    assert not compute_gate_inclusion(df, [no_cols]).any()
    assert not compute_gate_inclusion(df, [absent]).any()


def test_inclusion_no_gates_is_all_false():
    assert compute_gate_inclusion(_df(), []).tolist() == [False] * 5


# ── Gate_Pass inclusion layer ─────────────────────────────────────────────────

def test_gate_pass_layer_filters_by_default_but_not_for_scatter():
    from well_viewer.data.data_loading import df_included_mask

    df = pd.DataFrame({
        "Included":  [1, 1, 1, 1],
        "QC_Pass":   [1, 1, 1, 0],
        "Gate_Pass": [1, 0, 1, 1],
    })
    # Default composes Gate_Pass: rows 0 and 2 (row 1 gated out, row 3 QC-out).
    assert df_included_mask(df).tolist() == [True, False, True, False]
    # The per-cell scatter opts out of Gate_Pass → full cloud (QC still applies).
    assert df_included_mask(df, include_gate=False).tolist() == [True, True, True, False]


def test_gate_pass_absent_is_all_pass():
    from well_viewer.data.data_loading import df_included_mask

    df = pd.DataFrame({"Included": [1, 1], "QC_Pass": [1, 1]})
    assert df_included_mask(df).tolist() == [True, True]


# ── persistence round-trip ────────────────────────────────────────────────────

def test_gating_block_round_trips_scatter_gates(tmp_path):
    from well_viewer.domain.gating_state import (
        build_gating_block,
        read_gating_params,
        save_gating_to_pipeline_info,
    )

    gates = [{"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0,
              "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}]
    block = build_gating_block({}, {}, scatter_gates=gates, use_scatter_gates=True)
    assert block["use_scatter_gates"] is True
    assert block["scatter_gates"] == gates

    # save merges into an existing sidecar; seed one.
    (tmp_path / "pipeline_info.json").write_text(json.dumps({"channels": ["gfp"]}))
    save_gating_to_pipeline_info(tmp_path, block)

    loaded = read_gating_params(tmp_path)
    assert loaded["use_scatter_gates"] is True
    assert loaded["scatter_gates"] == gates


def test_gating_block_omits_gates_when_mode_off():
    from well_viewer.domain.gating_state import build_gating_block

    gates = [{"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0,
              "x_col": "a", "y_col": "b"}]
    # Mode off, or no gates → never persisted.
    assert build_gating_block({}, {}, scatter_gates=gates, use_scatter_gates=False) == {}
    assert build_gating_block({}, {}, scatter_gates=[], use_scatter_gates=True) == {}
