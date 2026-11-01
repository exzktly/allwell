"""'Deselect all' (select_none) honours the current tab's selection mode.

Regression: on a per-well tab (Cell Browser / Home / Review CSV)
with saved groups also present, select_none used to only hide the groups and
leave ``selected_wells`` — so the button looked broken. It must clear the
per-well selection on those tabs, and still hide groups on rep-mode plot tabs.
"""

from __future__ import annotations

from types import SimpleNamespace

from review.core.selection_controller import select_none


def _mk_app(tab, *, selections, selected):
    sel = SimpleNamespace(
        selections=selections,
        selected_wells=set(selected),
        active_solo_wells=set(),
        last_sel=None,
        prev_sel=set(),
    )

    def _clear():
        sel.selected_wells = set()
        sel.last_sel = None
        sel.prev_sel = set()
    sel.clear_selection = _clear

    emitted = []
    app = SimpleNamespace(
        _app_state=SimpleNamespace(selection=sel),
        _current_centre_tab=lambda: tab,
        _refresh_sidebar_map=lambda: None,
        _invalidate_stats_cache=lambda: None,
        _rebuild_all=lambda **kw: None,
        _event_bus=SimpleNamespace(
            selection_changed=SimpleNamespace(emit=lambda s: emitted.append(s))),
    )
    app._emitted = emitted
    app.window = app
    return app, sel


def test_per_well_tab_clears_wells_even_with_groups_present():
    # Cell Browser is a per-well tab (fallback _PER_WELL_TAB_NAMES, no registry).
    app, sel = _mk_app(
        "Cell Browser",
        selections=[{"name": "g", "wells": ["A1"], "hidden": False}],
        selected=["A1", "B2"],
    )
    select_none(app)
    assert sel.selected_wells == set()          # the actual fix
    assert app._emitted                          # selection_changed fired


def test_rep_mode_plot_tab_hides_groups():
    app, sel = _mk_app(
        "Bar Plots",
        selections=[{"name": "g", "wells": ["A1"], "hidden": False}],
        selected=["A1"],
    )
    select_none(app)
    assert all(s.get("hidden") for s in sel.selections)   # groups hidden (rep mode)


def test_no_groups_clears_wells():
    app, sel = _mk_app("Cell Browser", selections=[], selected=["A1", "B2"])
    select_none(app)
    assert sel.selected_wells == set()


# ── **All** follows the same rule (the selection footer offers both) ─────────

def _mk_app_for_all(tab, *, selections, loaded):
    app, sel = _mk_app(tab, selections=selections, selected=[])
    app._app_state.dataset = SimpleNamespace(well_paths={w: None for w in loaded})
    sel.select_wells = lambda wells: setattr(sel, "selected_wells", set(wells))
    return app, sel


def test_all_on_a_per_well_tab_selects_wells_even_with_groups_present():
    """The footer's **All** on Cell Browser with a hidden group defined: the
    plate there selects wells, so every loaded well is selected and the group's
    visibility is left alone. Before, ``select_all`` asked only whether groups
    existed, showed the group and selected nothing."""
    from review.core.selection_controller import select_all
    app, sel = _mk_app_for_all(
        "Cell Browser",
        selections=[{"name": "g", "wells": ["A1"], "hidden": True}],
        loaded=["A1", "B2", "C3"],
    )
    select_all(app)
    assert sel.selected_wells == {"A1", "B2", "C3"}
    assert sel.selections[0]["hidden"] is True
    assert app._emitted


def test_all_on_a_plot_tab_in_group_mode_shows_every_group():
    from review.core.selection_controller import select_all
    app, sel = _mk_app_for_all(
        "Bar Plots",
        selections=[{"name": "g", "wells": ["A1"], "hidden": True},
                    {"name": "h", "wells": ["B2"], "hidden": True}],
        loaded=["A1", "B2", "C3"],
    )
    select_all(app)
    assert [s["hidden"] for s in sel.selections] == [False, False]
    assert sel.selected_wells == set()
