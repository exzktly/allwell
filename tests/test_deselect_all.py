"""The selection footer's **All** / **None** act on what the plate shows.

Both follow the plate's own mode decision
(``selection_controller._plate_passive_in_group_mode``). On a well-editing page
(Home / Review CSV) the plate selects wells even with groups defined, so **None**
clears ``selected_wells`` and **All** selects every well — the original
regression was **None** hiding groups there and looking broken. Everywhere else
a plate with groups paints groups (Cell Browser mirrors them), so **All** /
**None** show / hide every group.

These used to key on ``per_well_selection`` instead, which also covers Cell
Browser, Multivariate, Spatial and Nanowells — pages whose plate stays in group
mode. **All** there wrote wells no plate drew and left every group hidden, and
the plate corner (which calls ``select_all`` with every group hidden) could no
longer bring them back. Two tests below pinned that behaviour on Cell Browser;
they are rewritten around the plate's rule.
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


def test_well_editing_tab_clears_wells_even_with_groups_present():
    app, sel = _mk_app(
        "Review CSV",
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


def test_all_on_a_well_editing_tab_selects_wells_even_with_groups_present():
    """Review CSV's plate selects wells with a group defined: **All** selects
    every loaded well and leaves the group's visibility alone."""
    from review.core.selection_controller import select_all
    app, sel = _mk_app_for_all(
        "Review CSV",
        selections=[{"name": "g", "wells": ["A1"], "hidden": True}],
        loaded=["A1", "B2", "C3"],
    )
    select_all(app)
    assert sel.selected_wells == {"A1", "B2", "C3"}
    assert sel.selections[0]["hidden"] is True
    assert app._emitted


def test_on_cell_browser_all_and_none_act_on_the_groups_the_plate_shows():
    """Cell Browser is a per-well page whose plate still paints groups. **All**
    must show the hidden groups — the plate corner calls it to bring them back —
    and **None** hide them, not write a well selection nothing draws."""
    from review.core.selection_controller import select_all
    app, sel = _mk_app_for_all(
        "Cell Browser",
        selections=[{"name": "g", "wells": ["A1"], "hidden": True}],
        loaded=["A1", "B2", "C3"],
    )
    select_all(app)
    assert sel.selections[0]["hidden"] is False
    assert sel.selected_wells == set()
    select_none(app)
    assert sel.selections[0]["hidden"] is True


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
