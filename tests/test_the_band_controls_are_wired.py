"""The three band combos are connected to something, on the real app.

`Group by`, `Colour by` and `Split by` are cross-tab controls whose entire
mechanism is `currentIndexChanged`, and the connections live in one place —
`core/centre_view.build_centre`. A reviewer deleted all three `connect` lines in
a worktree of HEAD and ran the covering fast-lane files (113 passed) and then
the slow lane's `test_app_smoke.py` (6 passed). Cutting the wiring for three
brand-new cross-tab controls was invisible to both lanes.

It was invisible because every existing test builds its **own** combo and wires
it itself — `test_group_by_variable`'s `_band_ctx` never connects at all and
calls `set_group_by_variable` directly, and `test_colour_and_split_by_variable`'s
`band` fixture re-implements the connection inside the fixture while its
docstring says it is "wired the way `centre_view` wires them". §7.6 #2: the
convenient seam sat underneath the thing that is actually wired up, so every
test entered one step past the bug.

So this file pays the fixture cost once, on a real `viewer_app`, and drives the
widgets the app itself built. Everything else may stay cheap.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt

VARIABLES = {
    "schema_version": 1,
    "variables": [
        {"id": "dose", "name": "Dose", "kind": "number", "unit": "µM",
         "values": [{"id": "v1", "label": "1"}, {"id": "v2", "label": "10"}]},
        {"id": "line", "name": "Cell line", "kind": "text",
         "values": [{"id": "h", "label": "HeLa"}, {"id": "u", "label": "U2OS"}]},
    ],
    "assignments": {},
}


def _with_variables(app):
    import well_viewer.domain.sample_variables as sv
    from well_viewer.core.group_by_controller import (
        refresh_decoration_combos, refresh_group_by_combo,
    )

    ctx = app._ctx
    wells = sorted(ctx.state.dataset.well_paths or {})
    vset = sv.from_block(VARIABLES)
    vset = sv.assign(vset, "dose", wells, "v1")
    vset = sv.assign(vset, "line", wells, "h")
    ctx.state.selection.variables = vset
    refresh_group_by_combo(ctx)
    refresh_decoration_combos(ctx)
    return ctx


@pytest.mark.parametrize(
    "widget_name, field",
    [
        ("_plotting_groupby_cb", "group_by_variable"),
        ("_plotting_colourby_cb", "colour_by_variable"),
        ("_plotting_splitby_cb", "split_by_variable"),
    ],
)
def test_choosing_a_variable_in_the_band_reaches_the_state(viewer_app, widget_name, field):
    """Driven through ``setCurrentIndex`` on the combo the app built, so the
    signal path the user's click takes is the one under test. Deleting the
    ``connect`` line for any of the three fails exactly this case."""
    ctx = _with_variables(viewer_app)
    combo = ctx.widgets.get_or_none(widget_name)
    assert combo is not None, f"{widget_name} was never registered"

    index = combo.findData("dose")
    assert index > 0, f"{widget_name} does not offer the variable"
    setattr(ctx.state.plot, field, "")

    combo.setCurrentIndex(index)

    assert getattr(ctx.state.plot, field) == "dose", (
        f"{widget_name} is not connected — choosing a variable changed nothing"
    )


def test_choosing_the_first_entry_clears_the_setting(viewer_app):
    """The other direction. A connection that only ever *set* a value would
    leave the plot grouped after the user chose "Wells / groups"."""
    ctx = _with_variables(viewer_app)
    combo = ctx.widgets.get_or_none("_plotting_groupby_cb")
    combo.setCurrentIndex(combo.findData("dose"))
    assert ctx.state.plot.group_by_variable == "dose"

    combo.setCurrentIndex(0)

    assert ctx.state.plot.group_by_variable == ""


def test_the_decorate_button_is_offered_only_where_it_does_something(viewer_app):
    """Colour by / Split by are read by one renderer. The popover sits on the
    page-level band, which is shared by every Plotting leaf and is never hidden
    by the per-leaf machinery — so it appeared on all six and did nothing on
    five, against the band's own stated rule."""
    from well_viewer.core.group_by_controller import DECORATED_TABS
    from well_viewer.persistence import view_state

    ctx = _with_variables(viewer_app)
    button = ctx.widgets.get_or_none("_plotting_decorate_btn")
    assert button is not None

    # ``isHidden`` rather than ``isVisible``: the test window is never shown,
    # so ``isVisible`` is False for every widget in it regardless of what the
    # code decided. ``isHidden`` is the explicit hide state, which is the thing
    # under test.
    view_state._activate_tab(ctx, "Bar Plots")
    assert "Bar Plots" in DECORATED_TABS
    assert not button.isHidden(), "hidden on the one tab that honours it"

    view_state._activate_tab(ctx, "Line Graphs")
    assert "Line Graphs" not in DECORATED_TABS
    assert button.isHidden(), (
        "offered on a tab whose renderer never reads Colour by or Split by"
    )
