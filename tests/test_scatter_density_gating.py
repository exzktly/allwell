"""Unit tests for the scatter density + gating helpers (pure numpy)."""

from __future__ import annotations

import numpy as np

from well_viewer.features.scatter.gating import (
    compute_gate_stats,
    compute_point_density,
)
from well_viewer.features.scatter.state import ScatterState
from well_viewer.core.export_prefs_state import ExportPrefsState
from well_viewer.core.cell_gating_state import CellGatingState


def _mk_ctx(app):
    """A ctx wrapper over a SimpleNamespace fake app: state/runtime/widgets all
    resolve through the app's ``_<name>`` attributes, matching how the tests set
    (and assert on) them directly.

    Epic-cellgating: the scatter-gate geometry is typed state on
    ``ctx.state.cell_gating.scatter_gates``; ensure the slice exists (seeded from
    a loose ``_scatter_gates`` kwarg for the minimal fakes that set one)."""
    from types import SimpleNamespace
    from well_viewer.core.app_runtime import AppRuntime
    st = getattr(app, "_app_state", None)
    if st is None:
        st = SimpleNamespace()
        app._app_state = st
    if getattr(st, "cell_gating", None) is None:
        st.cell_gating = CellGatingState(
            scatter_gates=list(getattr(app, "_scatter_gates", []) or []))
    return SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1],
        ),
        notify=SimpleNamespace(set_status=lambda _m: None),
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


def test_density_log_plus1_includes_nonpositive():
    # Under the log10(x+1) display transform, zero / negative x are clipped to 0
    # (→ log10(1)=0) rather than masked, so every point gets a finite density and
    # zero-valued cells stay visible. No crash.
    x = np.array([-1.0, 0.0, 1.0, 10.0, 100.0, 1000.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = compute_point_density(x, y, x_log=True)
    assert out.shape == x.shape
    assert np.all(np.isfinite(out))


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
        dataset=SimpleNamespace(well_paths={"A01": None}, well_labels={},
                                cache={"A01": df}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01"], selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._ax_scatter = ax
    app._scatter_fig = fig
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._app_state.export_prefs.style_prefs = prefs
    app._app_state.cell_gating.scatter_gates = list(gates)
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._ctx = _mk_ctx(app)
    return app, ax


def test_redraw_density_creates_density_collection_and_gate():
    from matplotlib.patches import Rectangle

    from well_viewer.features.scatter.controller import redraw_scatter

    gate = {"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}
    app, ax = _render_app(density=True, gates=[gate])
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
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
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    assert len(ax.lines) >= 1  # solid Line2D markers
    assert not any(getattr(c, "_aw_density_collection", False) for c in ax.collections)


def test_scatter_legend_uses_sample_definition_label():
    """Per-well scatter legend text comes from the sample-definition label when
    one is defined (``_well_display_label``), not the raw well token. The Line2D
    gid still keys off the raw token identity."""
    from well_viewer.features.scatter.controller import redraw_scatter

    app, ax = _render_app(density=False, gates=[])
    app._app_state.dataset.well_labels = {"A01": "Treated 5nM"}
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    labels = [ln.get_label() for ln in ax.lines]
    assert "Treated 5nM" in labels
    assert "A01" not in labels  # raw token no longer used as the legend text


def test_hidden_gates_are_not_drawn():
    """The 'Show gates' toggle (``_scatter_gates_hidden``) suppresses rendering
    without discarding the stored gates."""
    from matplotlib.patches import Rectangle

    from well_viewer.features.scatter.controller import redraw_scatter

    gate = {"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}
    app, ax = _render_app(density=False, gates=[gate])
    app._app_state.scatter.gates_hidden = True
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])

    assert app._app_state.cell_gating.scatter_gates == [gate]   # gate kept in memory
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
    app._ctx = _mk_ctx(app)
    _on_gate_drawn(app._ctx, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=10.0, ydata=10.0))

    assert len(app._app_state.cell_gating.scatter_gates) == 1
    assert calls == [1]


def test_separate_axes_layout_stacks_one_panel_per_group():
    """'Separate axes' layout renders one stacked panel per group — with
    click-to-view-cell enabled per panel — and switching back to Overlay
    collapses to a single axis."""
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
        dataset=SimpleNamespace(well_paths={"A01": None, "B02": None}, well_labels={}, cache={"A01": _df(), "B02": _df()}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01", "B02"], selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._ctx = _mk_ctx(app)
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: _df()
    app._rank_color_well = lambda label: "#3366cc"
    app._parse_rc = lambda label: (label, 0, 0)
    app._scatter_fig = fig
    app._ax_scatter = fig.axes[0]
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._app_state.export_prefs.style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    app._app_state.cell_gating.scatter_gates = []
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Separate axes")

    cols = ("w3cy5_mean_intensity", "w4gfp_mean_intensity")
    redraw_scatter(app._ctx, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])
    assert len(app._scatter_fig.axes) == 2                     # one panel per group
    # Click-to-view-cell now works per panel: each panel's interaction points
    # are recorded against its axis (axes_points), with the flattened union under
    # "points" and the active columns stamped for gate hit-testing.
    cache = app._scatter_interaction_cache
    assert [ax for ax, _pts in cache["axes_points"]] == list(app._scatter_fig.axes)
    assert len(cache["points"]) == 2 * n                       # both panels, flattened
    assert cache["col_x"] == "w3cy5_mean_intensity"
    assert cache["col_y"] == "w4gfp_mean_intensity"

    # Back to Overlay → collapses to one axis.
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Overlay")
    redraw_scatter(app._ctx, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])
    assert len(app._scatter_fig.axes) == 1


def test_gates_appear_on_every_separate_axis():
    """A drawn gate must render on each panel of the 'Separate axes' layout,
    each labelled with that panel's own in-gate percentage."""
    from types import SimpleNamespace

    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
    from well_viewer.features.scatter.controller import redraw_scatter

    n = 30
    rng = np.random.default_rng(5)

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
        dataset=SimpleNamespace(well_paths={"A01": None, "B02": None}, well_labels={}, cache={"A01": _df(), "B02": _df()}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01", "B02"], selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._ctx = _mk_ctx(app)
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: _df()
    app._rank_color_well = lambda label: "#3366cc"
    app._parse_rc = lambda label: (label, 0, 0)
    app._scatter_fig = fig
    app._ax_scatter = fig.axes[0]
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._app_state.export_prefs.style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    app._app_state.cell_gating.scatter_gates = [{"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}]
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Separate axes")

    cols = ("w3cy5_mean_intensity", "w4gfp_mean_intensity")
    redraw_scatter(app._ctx, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])

    axes = app._scatter_fig.axes
    assert len(axes) == 2
    for ax in axes:
        assert any(isinstance(p, Rectangle) and getattr(p, "_scatter_gate_artist", False)
                   for p in ax.patches), "gate rectangle missing on a panel"
        assert any(getattr(t, "_scatter_gate_artist", False)
                   for t in ax.texts), "gate % label missing on a panel"


# ── sidebar builds the scatter-only Density + Gates sections ──────────────────

def _sidebar_stub_app(fig):
    from types import SimpleNamespace
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None),
        plot=SimpleNamespace(line_order_rsets=[], line_order_wells=[], use_sem=True),
        selection=SimpleNamespace(selected_wells=["A01"]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._ctx = _mk_ctx(app)
    app._scatter_fig = fig  # makes _is_scatter_cells_fig / _supports_well_order true
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    # gate state the new section reads
    app._app_state.cell_gating.scatter_gates = []
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
    app._app_state.cell_gating.scatter_gates = [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]
    sb._on_clear_gates()
    assert app._app_state.cell_gating.scatter_gates == []


def test_draw_gate_is_one_shot_button(qtbot):
    """'Draw gate' is a push button (not a toggle): clicking arms a single
    drag, and the one-shot disarm restores the label."""
    from PySide6.QtWidgets import QPushButton
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasAgg(fig)
    app = _sidebar_stub_app(fig)
    app._ax_scatter = fig.axes[0]
    app._scatter_canvas = canvas
    app._scatter_gate_selector = None

    sb = ExportStyleSidebar(app, None, fig, None, default_name="scatter")
    qtbot.addWidget(sb)

    btn = sb._gate_draw_btn
    assert isinstance(btn, QPushButton)   # a button, not a ToggleSwitch
    btn.click()
    assert app._scatter_gate_draw_mode is True       # armed for one drag
    assert btn.text() != "Draw gate"                 # shows the armed hint
    # gating.py disarms after the gate is drawn → restore the label.
    app._scatter_gate_draw_sync(False)
    assert btn.text() == "Draw gate"


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
    app._app_state = SimpleNamespace(scatter=ScatterState(), cell_gating=CellGatingState())
    app._ax_scatter = ax
    app._scatter_canvas = canvas
    app._app_state.cell_gating.scatter_gates = [dict(gate)]
    app._scatter_gate_draw_mode = False
    app._scatter_gate_edit = None
    app._scatter_gate_ctx = ({}, [], "#888888")
    app._ctx = _mk_ctx(app)
    return app, ax


def _ev(ax, xdata, ydata, button=1):
    from types import SimpleNamespace
    px, py = ax.transData.transform((xdata, ydata))
    return SimpleNamespace(inaxes=ax, x=float(px), y=float(py),
                           xdata=xdata, ydata=ydata, button=button)


def test_gate_edge_at_detects_right_edge():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    hit = gate_edge_at(app._ctx, _ev(ax, 8, 5))
    assert hit == (0, "r")


def test_gate_edge_at_detects_top_right_corner():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    hit = gate_edge_at(app._ctx, _ev(ax, 8, 8))
    assert hit == (0, "tr")


def test_gate_edge_at_interior_returns_none():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    assert gate_edge_at(app._ctx, _ev(ax, 5, 5)) is None


def test_gate_edge_at_hidden_returns_none():
    from well_viewer.features.scatter.gating import gate_edge_at
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    app._app_state.scatter.gates_hidden = True
    assert gate_edge_at(app._ctx, _ev(ax, 8, 5)) is None


def test_gate_edit_resize_right_edge():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app._ctx, _ev(ax, 8, 5))
    assert app._scatter_gate_edit == {"idx": 0, "handle": "r"}
    on_gate_edit_motion(app._ctx, _ev(ax, 9, 5))
    on_gate_edit_release(app._ctx, _ev(ax, 9, 5))
    assert app._scatter_gate_edit is None
    g = app._app_state.cell_gating.scatter_gates[0]
    assert g["x1"] == 9 and g["x0"] == 2  # right edge moved, left unchanged


def test_gate_edit_corner_moves_both_axes():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app._ctx, _ev(ax, 8, 8))  # top-right corner
    on_gate_edit_motion(app._ctx, _ev(ax, 6, 7))
    on_gate_edit_release(app._ctx, _ev(ax, 6, 7))
    g = app._app_state.cell_gating.scatter_gates[0]
    assert g["x1"] == 6 and g["y1"] == 7 and g["x0"] == 2 and g["y0"] == 2


def test_gate_edit_collapse_removes_gate():
    from well_viewer.features.scatter.gating import (
        on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
    )
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    on_gate_edit_press(app._ctx, _ev(ax, 8, 5))     # grab right edge
    on_gate_edit_motion(app._ctx, _ev(ax, 2, 5))    # drag onto the left edge → zero width
    on_gate_edit_release(app._ctx, _ev(ax, 2, 5))
    assert app._app_state.cell_gating.scatter_gates == []  # collapsed gate dropped


def test_gate_edit_press_ignored_in_draw_mode():
    from well_viewer.features.scatter.gating import on_gate_edit_press
    app, ax = _edit_app_with_gate({"x0": 2, "y0": 2, "x1": 8, "y1": 8})
    app._scatter_gate_draw_mode = True
    on_gate_edit_press(app._ctx, _ev(ax, 8, 5))
    assert app._scatter_gate_edit is None  # draw mode owns the drag


# ── log defaults + faceted gate sync + stacked min-height ─────────────────────

def test_scatter_defaults_to_log_and_survives_restyle():
    """Scatter axes default to the log10(x+1) display transform (matplotlib
    "function" scale) and stay so even when the shared x_log/y_log pref (used by
    other plot types) is False."""
    from well_viewer.core.plot_style import apply_export_style_to_current
    from well_viewer.features.scatter.controller import redraw_scatter

    app, ax = _render_app(density=False, gates=[])
    # Shared keys are linear (their default) — scatter must ignore them.
    app._app_state.export_prefs.style_prefs["x_log"] = False
    app._app_state.export_prefs.style_prefs["y_log"] = False
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])
    assert ax.get_xscale() == "function"
    assert ax.get_yscale() == "function"
    # A later restyle (e.g. sidebar field change) must not revert to linear.
    apply_export_style_to_current(app._ctx, app._scatter_fig, app._scatter_canvas)
    assert ax.get_xscale() == "function" and ax.get_yscale() == "function"


def test_scatter_log_off_via_scatter_pref():
    from well_viewer.features.scatter.controller import redraw_scatter
    app, ax = _render_app(density=False, gates=[])
    app._app_state.export_prefs.style_prefs["scatter_x_log"] = False
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])
    assert ax.get_xscale() == "linear"
    assert ax.get_yscale() == "function"  # log10(y+1) display transform


def test_drawing_a_gate_exits_create_mode():
    from types import SimpleNamespace

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from well_viewer.features.scatter.gating import _on_gate_drawn

    fig = Figure(); ax = fig.add_subplot(1, 1, 1)
    synced = []
    app = SimpleNamespace(
        _scatter_gates=[], _ax_scatter=ax,
        _scatter_canvas=FigureCanvasAgg(fig),
        _scatter_gate_draw_mode=True,
        _scatter_gate_selector=None,
        _scatter_gate_count_cb=lambda: None,
        _scatter_gate_redraw_cb=lambda _a: None,
        _scatter_gate_draw_sync=lambda on: synced.append(on),
    )
    app._ctx = _mk_ctx(app)
    _on_gate_drawn(app._ctx, SimpleNamespace(xdata=0.0, ydata=0.0),
                   SimpleNamespace(xdata=5.0, ydata=5.0))
    assert len(app._app_state.cell_gating.scatter_gates) == 1
    assert app._scatter_gate_draw_mode is False   # auto-exited create mode
    assert synced == [False]                       # toggle synced off


def test_edit_drag_repaints_gate_on_all_separate_panels():
    """Resizing the gate (top panel) must update the gate on the panels below."""
    from types import SimpleNamespace

    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
    from well_viewer.features.scatter.controller import redraw_scatter
    from well_viewer.features.scatter.gating import redraw_gates_only

    n = 30
    rng = np.random.default_rng(11)

    def _df():
        return pd.DataFrame({
            "Included": [1] * n, "area_px": [200.0] * n,
            "timepoint_hours": [24.0] * n, "timepoint": ["24h"] * n,
            "filename": [f"f{i}.tif" for i in range(n)],
            "nucleus_id": [str(i) for i in range(n)],
            "w3cy5_mean_intensity": rng.normal(1000, 100, n).tolist(),
            "w4gfp_mean_intensity": rng.normal(1000, 100, n).tolist(),
        })

    fig = Figure(figsize=(6, 6)); fig.add_subplot(1, 1, 1)
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None, "B02": None}, well_labels={}, cache={"A01": _df(), "B02": _df()}),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=["A01", "B02"], selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._ctx = _mk_ctx(app)
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: _df()
    app._rank_color_well = lambda label: "#3366cc"
    app._parse_rc = lambda label: (label, 0, 0)
    app._scatter_fig = fig
    app._ax_scatter = fig.axes[0]
    app._scatter_canvas = FigureCanvasAgg(fig)
    app._app_state.export_prefs.style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    app._app_state.cell_gating.scatter_gates = [{"x0": 900.0, "y0": 900.0, "x1": 1100.0, "y1": 1100.0}]
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._scatter_layout_var = SimpleNamespace(currentText=lambda: "Separate axes")

    cols = ("w3cy5_mean_intensity", "w4gfp_mean_intensity")
    redraw_scatter(app._ctx, *cols, [24.0], well_colors=["#3366cc", "#cc3366"])
    assert len(app._scatter_gate_render) == 2     # one render entry per panel

    # Simulate a resize: move the gate's left edge, then repaint gates only.
    app._app_state.cell_gating.scatter_gates[0]["x0"] = 800.0
    redraw_gates_only(app._ctx)
    for ax in app._scatter_fig.axes:
        rects = [p for p in ax.patches
                 if isinstance(p, Rectangle) and getattr(p, "_scatter_gate_artist", False)]
        assert rects, "panel lost its gate after edit"
        assert any(abs(r.get_x() - 800.0) < 1e-6 for r in rects), \
            "panel below did not follow the top-panel resize"


def test_setup_axes_sets_per_panel_min_height():
    from types import SimpleNamespace

    from matplotlib.figure import Figure

    from well_viewer.features.scatter._layout import (
        SCATTER_PANEL_MIN_PX, _setup_scatter_axes,
    )

    heights = []
    app = SimpleNamespace(
        _scatter_fig=Figure(),
        _scatter_card=SimpleNamespace(setMinimumHeight=lambda h: heights.append(h)),
    )
    app._ctx = _mk_ctx(app)
    _setup_scatter_axes(app._ctx, 3)
    assert heights[-1] == 3 * SCATTER_PANEL_MIN_PX   # stacked → grows + scrolls
    _setup_scatter_axes(app._ctx, 1)
    assert heights[-1] == 0                           # overlay → fills viewport


# ── density mode respects the open-circle / marker-shape style ────────────────

def test_density_respects_open_circle_marker():
    """In density mode the open-circle ('o_open') style must produce hollow
    rings coloured by density — not stay filled (the reported regression)."""
    from well_viewer.features.scatter.controller import redraw_scatter

    app, ax = _render_app(density=True, gates=[])
    app._app_state.export_prefs.style_prefs["marker_style"] = "o_open"
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])
    coll = [c for c in ax.collections if getattr(c, "_aw_density_collection", False)][0]
    faces = coll.get_facecolors()
    edges = coll.get_edgecolors()
    # Open → no fill, and the ring colours vary per point (density gradient).
    assert len(faces) == 0 or bool(np.all(faces[:, 3] == 0))
    assert len(edges) > 1 and len(np.unique(edges[:, :3], axis=0)) > 1


def test_density_filled_keeps_per_point_gradient():
    from well_viewer.features.scatter.controller import redraw_scatter

    app, ax = _render_app(density=True, gates=[])
    app._app_state.export_prefs.style_prefs["marker_style"] = "o"
    redraw_scatter(app._ctx, "w3cy5_mean_intensity", "w4gfp_mean_intensity", [24.0],
                   well_colors=["#3366cc"])
    coll = [c for c in ax.collections if getattr(c, "_aw_density_collection", False)][0]
    faces = coll.get_facecolors()
    # Filled → per-point face colours preserved (gradient not collapsed).
    assert len(faces) > 1 and len(np.unique(faces[:, :3], axis=0)) > 1


def test_apply_axis_log_scale_plus1_keeps_data_raw():
    # The scatter log axis uses a log10(x+1) *display* transform (FuncScale): the
    # axis scale is "function", 0 is representable, and data coordinates stay raw
    # (so gates / clicks / exports are unaffected — "display-only, not saved").
    import numpy as np
    from matplotlib.figure import Figure
    from well_viewer.core.plot_style import apply_axis_log_scale

    ax = Figure().add_subplot(1, 1, 1)
    ax._aw_log_plus1 = True
    ax.scatter([0.0, 9.0, 99.0, 999.0], [0.0, 9.0, 99.0, 999.0])
    apply_axis_log_scale(ax, "x", True)
    apply_axis_log_scale(ax, "y", True)
    assert ax.get_xscale() == "function"
    # Zero (and every raw value) maps to a finite, monotonic display position.
    pos = [ax.transData.transform((x, 0.0))[0] for x in (0.0, 9.0, 99.0, 999.0)]
    assert all(np.isfinite(pos)) and pos[0] < pos[1] < pos[2] < pos[3]

    # Without the +1 flag, a plain log axis is used.
    ax2 = Figure().add_subplot(1, 1, 1)
    ax2.plot([1, 10, 100], [1, 10, 100])
    apply_axis_log_scale(ax2, "x", True)
    assert ax2.get_xscale() == "log"
    apply_axis_log_scale(ax2, "x", False)
    assert ax2.get_xscale() == "linear"
