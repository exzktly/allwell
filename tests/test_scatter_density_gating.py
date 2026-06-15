"""Unit tests for the scatter density + gating helpers (pure numpy)."""

from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.scatter.gating import (
    compute_gate_stats,
    compute_point_density,
)


# ── compute_point_density ────────────────────────────────────────────────────

def test_density_empty_returns_empty():
    out = compute_point_density([], [])
    assert isinstance(out, np.ndarray)
    assert out.size == 0


def test_density_few_points_uniform():
    out = compute_point_density([1.0, 2.0], [1.0, 2.0])
    assert out.shape == (2,)
    assert np.allclose(out, 0.5)


def test_density_normalized_range_and_length():
    rng = np.random.default_rng(0)
    x = rng.normal(size=500)
    y = rng.normal(size=500)
    out = compute_point_density(x, y)
    assert out.shape == (500,)
    assert out.min() >= 0.0 and out.max() <= 1.0
    # A normal cloud must have a spread of densities (not all equal).
    assert out.max() > out.min()


def test_density_center_denser_than_edge():
    # Tight central blob + a few far-flung outliers: center density > outliers.
    rng = np.random.default_rng(1)
    core = rng.normal(scale=0.2, size=(800, 2))
    outliers = np.array([[10.0, 10.0], [-10.0, -10.0], [10.0, -10.0]])
    pts = np.vstack([core, outliers])
    out = compute_point_density(pts[:, 0], pts[:, 1])
    assert out[:800].mean() > out[800:].mean()


def test_density_zero_variance_does_not_raise():
    # All x identical → singular KDE covariance; must fall back gracefully.
    out = compute_point_density([5.0] * 10, np.linspace(0, 1, 10))
    assert out.shape == (10,)
    assert np.all(np.isfinite(out))


def test_density_histogram_fallback_path():
    # max_kde_n=0 forces the O(N) histogram path regardless of scipy.
    rng = np.random.default_rng(2)
    x = rng.normal(size=300)
    y = rng.normal(size=300)
    out = compute_point_density(x, y, max_kde_n=0)
    assert out.shape == (300,)
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_density_log_masks_nonpositive():
    # Non-positive x under x_log get the minimum (0.0) density, no crash.
    x = np.array([-1.0, 0.0, 1.0, 10.0, 100.0, 1000.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = compute_point_density(x, y, x_log=True)
    assert out.shape == x.shape
    assert out[0] == 0.0 and out[1] == 0.0  # masked-out non-positive points


# ── compute_gate_stats ───────────────────────────────────────────────────────

def _gate(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_gate_single_group_single_tp():
    # 4 points, 2 inside the unit box → 50.0%.
    data = {"A1": {"x": [0.5, 0.5, 5.0, 5.0], "y": [0.5, 0.5, 5.0, 5.0],
                   "tp": [0.0, 0.0, 0.0, 0.0], "color": "#111111"}}
    rows = compute_gate_stats(data, _gate(0, 0, 1, 1), [0.0])
    assert rows == [("50.0%", None, 0)]


def test_gate_multiple_groups_per_group_pct():
    data = {
        "A1": {"x": [0.5, 0.5], "y": [0.5, 0.5], "tp": [0.0, 0.0], "color": "#aa0000"},
        "B2": {"x": [0.5, 9.0, 9.0, 9.0], "y": [0.5, 9.0, 9.0, 9.0],
               "tp": [0.0, 0.0, 0.0, 0.0], "color": "#0000aa"},
    }
    rows = compute_gate_stats(data, _gate(0, 0, 1, 1), [0.0])
    # A1: 2/2 = 100%, B2: 1/4 = 25%.
    assert ("A1: 100.0%", "#aa0000", 0) in rows
    assert ("B2: 25.0%", "#0000aa", 0) in rows


def test_gate_single_group_multi_tp():
    data = {"A1": {
        "x": [0.5, 0.5, 9.0, 0.5],
        "y": [0.5, 0.5, 9.0, 0.5],
        "tp": [0.0, 0.0, 0.0, 24.0],
        "color": "#123456",
    }}
    rows = compute_gate_stats(data, _gate(0, 0, 1, 1), [0.0, 24.0])
    # t=0h: 2/3 inside → 66.7%; t=24h: 1/1 → 100%.
    assert ("t=0h: 66.7%", "#123456", 0) in rows
    assert ("t=24h: 100.0%", "#123456", 0) in rows


def test_gate_multi_group_multi_tp_indented():
    data = {
        "A1": {"x": [0.5, 9.0], "y": [0.5, 9.0], "tp": [0.0, 24.0], "color": "#aa0000"},
        "B2": {"x": [0.5, 0.5], "y": [0.5, 0.5], "tp": [0.0, 24.0], "color": "#0000aa"},
    }
    rows = compute_gate_stats(data, _gate(0, 0, 1, 1), [0.0, 24.0])
    # Group lines at indent 0, per-tp sub-lines at indent 1.
    assert ("A1: 50.0%", "#aa0000", 0) in rows
    assert ("t=0h: 100.0%", "#aa0000", 1) in rows
    assert ("t=24h: 0.0%", "#aa0000", 1) in rows
    assert ("B2: 100.0%", "#0000aa", 0) in rows


def test_gate_empty_group_no_rows():
    data = {"A1": {"x": [], "y": [], "tp": [], "color": "#111111"}}
    rows = compute_gate_stats(data, _gate(0, 0, 1, 1), [0.0])
    assert rows == []


# ── redraw_scatter integration (headless Agg) ────────────────────────────────

def _render_app(*, density: bool, gates):
    """Stub app wired for ``redraw_scatter`` against a bare Agg figure."""
    from types import SimpleNamespace

    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS

    n = 60
    rng = np.random.default_rng(7)
    df = pd.DataFrame({
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [24.0] * n,
        "timepoint": ["24h"] * n,
        "filename": [f"img_{i}.tif" for i in range(n)],
        "nucleus_id": [str(i) for i in range(n)],
        "w3cy5_mean_intensity": rng.normal(1000, 100, n).tolist(),
        "w4gfp_mean_intensity": rng.normal(1000, 100, n).tolist(),
    })

    fig = Figure(figsize=(6, 4))
    ax = fig.add_subplot(1, 1, 1)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["scatter_density"] = bool(density)

    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01"]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
    )
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: df
    app._rank_color_well = lambda label: "#3366cc"
    app._parse_rc = lambda label: (label, 0, 0)
    app._ax_scatter = ax
    app._scatter_fig = fig
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._export_style_prefs = prefs
    app._scatter_gates = list(gates)
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    return app, ax


def test_redraw_density_creates_density_collection_and_gate():
    from matplotlib.patches import Rectangle

    from well_viewer.features.scatter.controller import redraw_scatter

    gate = {"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}
    app, ax = _render_app(density=True, gates=[gate])
    redraw_scatter(app, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    # Density mode → a PathCollection tagged for the export-style guard.
    assert any(getattr(c, "_aw_density_collection", False) for c in ax.collections)
    # Gate rectangle + label present.
    assert any(isinstance(p, Rectangle) and getattr(p, "_scatter_gate_artist", False)
               for p in ax.patches)
    assert any(getattr(t, "_scatter_gate_artist", False) for t in ax.texts)


def test_redraw_solid_mode_uses_lines_no_density():
    from well_viewer.features.scatter.controller import redraw_scatter

    app, ax = _render_app(density=False, gates=[])
    redraw_scatter(app, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    assert len(ax.lines) >= 1  # solid Line2D markers
    assert not any(getattr(c, "_aw_density_collection", False) for c in ax.collections)


def test_hidden_gates_are_not_drawn():
    """The 'Show gates' toggle (``_scatter_gates_hidden``) suppresses rendering
    without discarding the stored gates."""
    from matplotlib.patches import Rectangle

    from well_viewer.features.scatter.controller import redraw_scatter

    gate = {"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}
    app, ax = _render_app(density=False, gates=[gate])
    app._scatter_gates_hidden = True
    redraw_scatter(app, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    assert app._scatter_gates == [gate]   # gate kept in memory
    assert not any(isinstance(p, Rectangle) and getattr(p, "_scatter_gate_artist", False)
                   for p in ax.patches)
    assert not any(getattr(t, "_scatter_gate_artist", False) for t in ax.texts)


def test_drawing_a_gate_notifies_count_callback():
    """Drawing a gate fires the sidebar's gate-count refresh callback so the
    'N gate(s)' label stays accurate (was only updated at build / on clear)."""
    from types import SimpleNamespace

    from well_viewer.features.scatter.gating import _on_gate_drawn

    calls: list[int] = []
    app = SimpleNamespace(
        _scatter_gates=[],
        _scatter_gate_count_cb=lambda: calls.append(1),
        _scatter_gate_redraw_cb=lambda _a: None,
    )
    _on_gate_drawn(app, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=10.0, ydata=10.0))

    assert len(app._scatter_gates) == 1
    assert calls == [1]


def test_separate_axes_layout_stacks_one_panel_per_group():
    """'Separate axes' layout renders one stacked panel per group (view-only),
    and switching back to Overlay collapses to a single axis."""
    from types import SimpleNamespace

    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
    from well_viewer.features.scatter.controller import redraw_scatter

    n = 30
    rng = np.random.default_rng(3)

    def _df():
        return pd.DataFrame({
            "Included": [1] * n, "area_px": [200.0] * n,
            "timepoint_hours": [24.0] * n, "timepoint": ["24h"] * n,
            "filename": [f"f{i}.tif" for i in range(n)],
            "nucleus_id": [str(i) for i in range(n)],
            "w3cy5_mean_intensity": rng.normal(1000, 100, n).tolist(),
            "w4gfp_mean_intensity": rng.normal(1000, 100, n).tolist(),
        })

    fig = Figure(figsize=(6, 6))
    fig.add_subplot(1, 1, 1)
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None, "B02": None}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01", "B02"]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
    )
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: _df()
    app._rank_color_well = lambda label: "#3366cc"
    app._parse_rc = lambda label: (label, 0, 0)
    app._scatter_fig = fig
    app._ax_scatter = fig.axes[0]
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._export_style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    app._scatter_gates = []
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Separate axes")

    cols = ("w3cy5_mean_intensity", "w4gfp_mean_intensity")
    redraw_scatter(app, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])
    assert len(app._scatter_fig.axes) == 2                     # one panel per group
    assert app._scatter_interaction_cache == {"points": []}    # view-only

    # Back to Overlay → collapses to one axis.
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Overlay")
    redraw_scatter(app, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])
    assert len(app._scatter_fig.axes) == 1


# ── sidebar builds the scatter-only Density + Gates sections ──────────────────

def _sidebar_stub_app(fig):
    from types import SimpleNamespace
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None),
        plot=SimpleNamespace(line_order_rsets=[], line_order_wells=[], use_sem=True),
        selection=SimpleNamespace(selected_wells=["A01"]),
    )
    app._scatter_fig = fig  # makes _is_scatter_cells_fig / _supports_well_order true
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    # gate state the new section reads
    app._scatter_gates = []
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    return app


def test_sidebar_builds_scatter_density_and_gate_sections(qtbot):
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _sidebar_stub_app(fig)

    sb = ExportStyleSidebar(app, None, fig, None, default_name="scatter")
    qtbot.addWidget(sb)

    # Scatter-only helpers wired and the density pref registered for persistence.
    assert sb._is_scatter_cells_fig() is True
    assert "scatter_density" in sb._getters
    assert sb._gate_count_text() == "0 gate(s)"
    # Clearing gates through the sidebar updates the count label without raising.
    app._scatter_gates = [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]
    sb._on_clear_gates()
    assert app._scatter_gates == []


# ── gate editing (drag edges / corners) ──────────────────────────────────────

def _edit_app_with_gate(gate, xlim=(0.0, 10.0), ylim=(0.0, 10.0)):
    """Headless Agg app with one gate, ready for hit-test / drag handlers."""
    from types import SimpleNamespace

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6, 4))
    ax = fig.add_subplot(1, 1, 1)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    canvas = FigureCanvasAgg(fig)
    canvas.draw()  # populate transData

    app = SimpleNamespace()
    app._ax_scatter = ax
    app._scatter_canvas = canvas
    app._scatter_gates = [dict(gate)]
    app._scatter_gates_hidden = False
    app._scatter_gate_draw_mode = False
    app._scatter_gate_edit = None
    app._scatter_gate_ctx = ({}, [], "#888888")
    return app, ax


def _ev(ax, xdata, ydata, button=1):
    from types import SimpleNamespace
    px, py = ax.transData.transform((xdata, ydata))
    return SimpleNamespace(inaxes=ax, x=float(px), y=float(py),
                           xdata=xdata, ydata=ydata, button=button)


def test_gate_edge_at_detects_right_edge():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    hit = gate_edge_at(app, _ev(ax, 8, 5))
    assert hit == (0, "r")


def test_gate_edge_at_detects_top_right_corner():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    hit = gate_edge_at(app, _ev(ax, 8, 8))
    assert hit == (0, "tr")


def test_gate_edge_at_interior_returns_none():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    assert gate_edge_at(app, _ev(ax, 5, 5)) is None


def test_gate_edge_at_hidden_returns_none():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    app._scatter_gates_hidden = True
    assert gate_edge_at(app, _ev(ax, 8, 5)) is None


def test_gate_edit_resize_right_edge():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app, _ev(ax, 8, 5))
    assert app._scatter_gate_edit == {"idx": 0, "handle": "r"}
    on_gate_edit_motion(app, _ev(ax, 9, 5))
    on_gate_edit_release(app, _ev(ax, 9, 5))
    assert app._scatter_gate_edit is None
    g = app._scatter_gates[0]
    assert g["x1"] == 9 and g["x0"] == 2  # right edge moved, left unchanged


def test_gate_edit_corner_moves_both_axes():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app, _ev(ax, 8, 8))  # top-right corner
    on_gate_edit_motion(app, _ev(ax, 6, 7))
    on_gate_edit_release(app, _ev(ax, 6, 7))
    g = app._scatter_gates[0]
    assert g["x1"] == 6 and g["y1"] == 7 and g["x0"] == 2 and g["y0"] == 2


def test_gate_edit_collapse_removes_gate():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app, _ev(ax, 8, 5))     # grab right edge
    on_gate_edit_motion(app, _ev(ax, 2, 5))    # drag onto the left edge → zero width
    on_gate_edit_release(app, _ev(ax, 2, 5))
    assert app._scatter_gates == []  # collapsed gate dropped


def test_gate_edit_press_ignored_in_draw_mode():
    from well_viewer.features.scatter.gating import on_gate_edit_press
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    app._scatter_gate_draw_mode = True
    on_gate_edit_press(app, _ev(ax, 8, 5))
    assert app._scatter_gate_edit is None  # draw mode owns the drag
