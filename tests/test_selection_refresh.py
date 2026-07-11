"""Characterization harness for selection-change refresh.

``selection_changed`` is the EventBus signal a selection mutation emits;
``SelectionRefreshCoordinator`` runs ``apply_selection_refresh`` in response,
which refreshes the sidebar plate-map and dispatches a per-tab view refresh.

These tests pin that dispatch — one assertion per tab branch — on a real
loaded ``WellViewerApp``. They exercise the whole bus path: a selection
mutator (``select_all`` / ``select_none`` / ``on_plate_sel_change``) emits the
signal, the wired coordinator receives it, ``apply_selection_refresh``
dispatches. This guards the Phase 3b migration from the old direct
``_refresh_after_selection_change`` call to the bus-driven path.
"""

from __future__ import annotations

import pytest

from well_viewer.core.selection_controller import select_all, select_none
from well_viewer.core.selection_controller import on_plate_sel_change

pytestmark = pytest.mark.qt


class _CallCounter:
    """Wrap named methods on an object and tally invocations (calls through)."""

    def __init__(self, obj, names) -> None:
        self.counts = {n: 0 for n in names}
        for name in names:
            setattr(obj, name, self._wrap(name, getattr(obj, name)))

    def _wrap(self, name, original):
        def _counted(*args, **kwargs):
            self.counts[name] += 1
            return original(*args, **kwargs)
        return _counted


def _show_plotting_tab(app, leaf: str) -> None:
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName(leaf)
    assert app._current_centre_tab() == leaf


# ── Sidebar refresh runs for every selection change ──────────────────────────

def test_selection_change_always_refreshes_sidebar(loaded_app):
    _show_plotting_tab(loaded_app, "Line Graphs")
    counter = _CallCounter(loaded_app, ["_refresh_sidebar_map"])
    select_all(loaded_app._ctx)
    assert counter.counts["_refresh_sidebar_map"] == 1


def test_on_plate_sel_change_triggers_refresh(loaded_app):
    _show_plotting_tab(loaded_app, "Line Graphs")
    counter = _CallCounter(loaded_app, ["_refresh_sidebar_map", "_redraw"])
    on_plate_sel_change(loaded_app._ctx)
    assert counter.counts["_refresh_sidebar_map"] == 1
    assert counter.counts["_redraw"] == 1


# ── Per-tab dispatch branches ────────────────────────────────────────────────

def test_bar_plots_branch_redraws_bars(loaded_app, monkeypatch):
    # Dispatch now routes through the registry's on_selection_changed hook
    # (_sel_bar_plots), which imports redraw_bars from the renderer at call
    # time — so patch the canonical renderer symbol. Both the tab-show and the
    # selection paths use it now, so reset after the tab switch (as the scatter
    # test below does) to count only the selection-driven redraw.
    calls = {"bars": 0, "redraw": 0}
    monkeypatch.setattr("well_viewer.features.barplot.renderer.redraw_bars",
                        lambda app: calls.__setitem__("bars", calls["bars"] + 1))
    _show_plotting_tab(loaded_app, "Bar Plots")
    calls["bars"] = 0  # drop the redraw the tab switch itself triggered
    counter = _CallCounter(loaded_app, ["_redraw"])
    select_none(loaded_app._ctx)
    assert calls["bars"] == 1
    assert counter.counts["_redraw"] == 0


def test_line_graphs_branch_redraws_line(loaded_app, monkeypatch):
    calls = {"bars": 0}
    monkeypatch.setattr("well_viewer.features.barplot.renderer.redraw_bars",
                        lambda app: calls.__setitem__("bars", calls["bars"] + 1))
    _show_plotting_tab(loaded_app, "Line Graphs")
    counter = _CallCounter(loaded_app, ["_redraw"])
    select_all(loaded_app._ctx)
    assert counter.counts["_redraw"] == 1
    assert calls["bars"] == 0


def test_review_csv_branch_refreshes_csv(loaded_app):
    assert loaded_app._notebook.setCurrentByName("Review CSV")
    counter = _CallCounter(loaded_app, ["_refresh_review_csv", "_redraw"])
    select_all(loaded_app._ctx)
    assert counter.counts["_refresh_review_csv"] == 1
    assert counter.counts["_redraw"] == 0


def test_scatter_branch_redraws_scatter(loaded_app, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "well_viewer.features.scatter.tab_view.scatter_redraw_active",
        lambda app: calls.append(app),
    )
    _show_plotting_tab(loaded_app, "Scatter Plot")
    calls.clear()  # drop the redraw the tab switch itself triggered
    select_all(loaded_app._ctx)
    assert len(calls) == 1


def test_smfish_branch_syncs(loaded_app, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "well_viewer.features.smfish.tab_view.smfish_sync_from_app",
        lambda app: calls.append(app),
    )
    assert loaded_app._notebook.setCurrentByName("smFISH")
    assert loaded_app._current_centre_tab() == "smFISH"
    calls.clear()  # drop the sync the tab switch itself triggered
    select_all(loaded_app._ctx)
    assert len(calls) == 1
