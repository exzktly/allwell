"""The Properties panel outlives the dicts it reads, so it must not hold them.

Reported as "changing any limits or toggling x log/y log does absolutely
nothing" on Response Curves, with the rest of the panel dead too and only the
curve-colour control still working. That last detail is the whole diagnosis: the
colour segment writes straight to ``PlotState``, and everything that was dead
writes through ``figure_prefs``.

``figure_prefs`` used to hand back a view holding the two dicts it resolved to
at that moment. The panel is built once per figure and cached for the life of
the process, and loading a dataset replaces both stores wholesale —
``view_state.restore`` rebuilds ``axis_prefs`` as ``{str(k): dict(v)}``, and
``ExportPrefsState.reset_for_new_dataset`` rebuilds it without the entries that
emptied. From then on the panel wrote every limit, log toggle, log base and
per-axis tick size into a dict nothing read, and reported success: the fields
kept the values and the figure never moved.

So the view resolves the stores per access, and the panel re-seeds its
preference-backed fields when it is reopened — the second half matters because
``_persist`` writes *every* field on any edit, so a panel still showing the
previous plate's limits would put them back on the next plate the first time
the user changed anything at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_state import PlotState
from review.core.plot_style import figure_prefs
from review.features.scatter.state import ScatterState

pytestmark = pytest.mark.qt


def _mk_ctx(app):
    from review.core.app_runtime import AppRuntime
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


def _stub_app():
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None),
        plot=PlotState(),
        selection=SimpleNamespace(selected_wells=["A01"]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._app_state.cell_gating.scatter_gates = []
    app._ctx = _mk_ctx(app)
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._statuses = []
    app._set_status = app._statuses.append
    app._current_centre_tab = lambda: "Response Curves"
    app._redraw = lambda: None
    return app


def _figure():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
    return fig, ax


def _a_dataset_load(ctx, entries: dict) -> None:
    """What a load does to the store: a whole new dict of whole new dicts.

    Spelled out here rather than by calling ``view_state.restore`` so the test
    states the property it depends on — that the objects are replaced — instead
    of depending on a loader whose other work is irrelevant to it. The shape is
    copied from ``view_state.restore``'s own line.
    """
    ctx.state.export_prefs.axis_prefs = {
        str(k): dict(v) for k, v in entries.items()
    }


# ── the view itself ──────────────────────────────────────────────────────────

def test_a_view_keeps_working_after_the_store_is_replaced():
    """The value, on both sides: what the panel writes is what a later reader
    finds. Asserting only that the write "did not raise" would pass against the
    orphan, which raises nothing and loses everything."""
    app = _stub_app()
    ctx = app._ctx
    fig, _ax = _figure()
    app._line_fig = fig

    held = figure_prefs(ctx, fig)
    _a_dataset_load(ctx, {"line": {"x_log": True}})

    held["y_lim_max"] = "40"
    assert figure_prefs(ctx, fig).get("y_lim_max") == "40", (
        "the panel's write went into a dict the next reader does not have")
    assert ctx.state.export_prefs.axis_prefs["line"]["y_lim_max"] == "40"


def test_a_view_reads_what_the_load_put_there():
    """The other direction. A view that captured the old entry would go on
    reporting the pre-load value, so the panel would show one figure's settings
    over another's."""
    app = _stub_app()
    ctx = app._ctx
    fig, _ax = _figure()
    app._line_fig = fig

    held = figure_prefs(ctx, fig)
    held["y_lim_max"] = "40"
    _a_dataset_load(ctx, {"line": {"y_lim_max": "99"}})
    assert held.get("y_lim_max") == "99"


def test_the_global_style_survives_a_reseeded_store_too():
    """``_ensure_export_style_prefs`` can replace the global dict as well (it
    seeds it from ``None``), and a view that captured that one would drop every
    typography edit instead."""
    app = _stub_app()
    ctx = app._ctx
    fig, _ax = _figure()
    app._line_fig = fig

    held = figure_prefs(ctx, fig)
    ctx.state.export_prefs.style_prefs = None  # what a reset leaves behind
    held["axis_label_size"] = 30
    assert figure_prefs(ctx, fig).get("axis_label_size") == 30


# ── and through the panel the user actually touches ──────────────────────────

def _build_sidebar(qtbot, monkeypatch):
    from review.features.export.sidebar_view import ExportStyleSidebar

    fig, ax = _figure()
    app = _stub_app()
    app._line_fig = fig
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        lambda self: True, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="line")
    qtbot.addWidget(sb)
    return app, sb, fig, ax


def test_a_limit_typed_after_a_load_reaches_the_store_the_redraw_reads(
        qtbot, monkeypatch):
    """End to end through the widget, because the store identity is only half
    of it: the panel holds the view, and the view has to still be pointing at
    the live entry when the user types into the field."""
    app, sb, fig, ax = _build_sidebar(qtbot, monkeypatch)
    _a_dataset_load(app._ctx, {"line": {}})

    sb._setters["y_lim_min"]("2")
    sb._setters["y_lim_max"]("4")

    assert figure_prefs(app._ctx, fig).get("y_lim_max") == "4", (
        "the typed limit never reached the store the next redraw reads")
    assert ax.get_ylim() == pytest.approx((2.0, 4.0)), (
        "the limit was stored but never applied to the axes")


def test_the_log_toggle_reaches_the_store_after_a_load(qtbot, monkeypatch):
    app, sb, fig, ax = _build_sidebar(qtbot, monkeypatch)
    _a_dataset_load(app._ctx, {"line": {}})

    sb._setters["y_log"](True)

    assert figure_prefs(app._ctx, fig).get("y_log") is True
    assert ax.get_yscale() == "log"


def test_reopening_the_panel_forgets_the_previous_plate_s_limits(
        qtbot, monkeypatch):
    """A dataset switch drops every axis limit, because a limit is in the units
    of the plate that was open. The cached panel still holds the fields, and
    ``_persist`` writes every field on any edit — so without the re-seed,
    changing the title size would put the dropped limits back onto the new
    plate. That is the defect the drop exists to prevent, arriving through the
    panel instead.
    """
    app, sb, fig, _ax = _build_sidebar(qtbot, monkeypatch)
    sb._setters["y_lim_min"]("2")
    sb._setters["y_lim_max"]("4")
    assert figure_prefs(app._ctx, fig).get("y_lim_max") == "4"

    # The switch: the limits are dropped from the store, the panel is not told.
    app._app_state.export_prefs.reset_for_new_dataset()
    sb._resync_pref_fields()

    assert sb._getters["y_lim_max"]() == "", (
        "the field still shows the previous plate's limit")
    sb._setters["title_size"](22)  # an edit about something else entirely
    assert figure_prefs(app._ctx, fig).get("y_lim_max", "") == "", (
        "an unrelated edit put the previous plate's limit back")
    assert not any(
        (b or {}).get("y_lim_max")
        for b in (figure_prefs(app._ctx, fig).get("axis_buckets") or {}).values()
    ), "the limit came back through a per-axis bucket"


def test_the_reseed_keeps_a_limit_the_store_still_holds(qtbot, monkeypatch):
    """The re-seed is a catch-up, not a reset. A limit still in the store has
    to survive it — an implementation that simply blanked the fields would pass
    the test above and quietly discard every setting on every panel open."""
    app, sb, fig, _ax = _build_sidebar(qtbot, monkeypatch)
    sb._setters["y_lim_min"]("2")
    sb._setters["y_lim_max"]("4")

    sb._resync_pref_fields()

    assert sb._getters["y_lim_min"]() == "2"
    assert sb._getters["y_lim_max"]() == "4"
