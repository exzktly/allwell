"""The Nanowells page follows replicate-mode group edits like the other per-well tabs (roll-up review X9, X10).

Its figure draws ``plot_group_well_lists`` — the visible sets plus the solo wells toggled on — so in replicate
mode its plate must paint that set and a click must toggle it (X9), and a group edit, which reaches the tabs
through ``rebuild_all`` and never emits ``selection_changed``, must refresh it while it is on screen (X10).
Driven on the real app, through the real controller entry points.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_on_the_nanowells_page_a_plate_click_in_replicate_mode_toggles_a_solo_well(loaded_app, monkeypatch):
    from review.core import selection_controller as sc

    app = loaded_app
    wells = sorted(app._app_state.dataset.well_paths)
    grouped, solo = wells[:2], wells[2]
    app._app_state.selection.selections = [{"name": "G", "wells": grouped, "hidden": False}]
    app._app_state.selection.active_solo_wells = set()
    monkeypatch.setattr(app, "_current_centre_tab", lambda: "Nanowells")
    sc.on_sidebar_plate_well_activated(app._ctx, solo)
    assert app._app_state.selection.active_solo_wells == {solo}


def test_a_group_edit_refreshes_the_nanowells_page_when_it_is_on_screen(loaded_app, monkeypatch):
    from review.core import selection_controller as sc

    app = loaded_app
    called = []
    orig_get = app._features.get
    assert orig_get("Nanowells").on_selection_changed is not None
    spy = SimpleNamespace(on_selection_changed=lambda _ctx: called.append("Nanowells"))
    monkeypatch.setattr(app._features, "get", lambda name: spy if name == "Nanowells" else orig_get(name))
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: name == "Nanowells")
    sc.rebuild_all(app._ctx)
    assert called == ["Nanowells"]
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: False)
    sc.rebuild_all(app._ctx)
    assert called == ["Nanowells"]
