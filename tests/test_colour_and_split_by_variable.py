"""Colour by and Split by: decoration that moves no numbers.

These two band controls change the ink a bar takes and the labelled band it
sits in, and nothing else. ``group_by_variable`` alone decides what a member
*is*, so a figure with both of these set draws exactly the bars it drew before
either was — which is the property most worth pinning, because a "decoration"
that quietly re-pooled the wells would be the worst possible version of this
feature.

The other half is §7.5 #5. ``collect_bar_items`` builds items in four branches
and the decoration is applied once over the finished list, so no branch can be
left out. The test that matters there is not that one branch works; it is that
every branch does.
"""

from __future__ import annotations

import pytest

from well_viewer.domain import sample_variables as sv
from well_viewer.domain import variable_series as vs
from well_viewer.features.barplot._decorate import (
    bar_legend_entries, decorate_bar_items,
)
from well_viewer.features.barplot.collect import (
    clustered_x_positions, cluster_centers, collect_bar_items,
)

from tests.conftest import MockApp, make_agg

_WELLS = ("A01", "A02", "B01", "B02")

_TWO_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
]


def _line(name="Cell line", pairs=(("HeLa", None), ("U2OS", None))):
    return sv.Variable(id=name.lower().replace(" ", "_"), name=name,
                       kind=sv.TEXT, unit="",
                       values=tuple(sv.VariableValue(f"{name[0]}{i}", lab, num)
                                    for i, (lab, num) in enumerate(pairs)))


def _ctx(*, selections=None, variable=None, assignments=None,
         colour_by="", split_by="", pool_reps=True):
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, 100.0 + 10 * i)]
                      for i, w in enumerate(_WELLS)},
        selections=selections if selections is not None else _TWO_SETS,
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = pool_reps
    ctx.state.plot.colour_by_variable = colour_by
    ctx.state.plot.split_by_variable = split_by
    if variable is not None:
        ctx.state.selection.variables = sv.VariableSet(
            variables=(variable,),
            assignments={variable.id: dict(assignments or {})})
    return ctx


def _items(ctx):
    _use_groups, items, _band = collect_bar_items(ctx, 0.0)
    return items


# ── neither control moves a number ──────────────────────────────────────────

def test_colouring_by_a_variable_changes_no_value():
    """The property the whole design rests on: decoration is not grouping."""
    variable = _line()
    kwargs = dict(selections=_TWO_SETS, variable=variable,
                  assignments={"A01": "C0", "A02": "C0",
                               "B01": "C1", "B02": "C1"})
    plain = _items(_ctx(**kwargs))
    coloured = _items(_ctx(**kwargs, colour_by=variable.id))

    assert [i.key for i in plain] == [i.key for i in coloured]
    assert [i.mean for i in plain] == [i.mean for i in coloured]


def test_splitting_by_a_variable_changes_no_value():
    variable = _line()
    kwargs = dict(selections=_TWO_SETS, variable=variable,
                  assignments={"A01": "C0", "A02": "C0",
                               "B01": "C1", "B02": "C1"})
    plain = _items(_ctx(**kwargs))
    split = _items(_ctx(**kwargs, split_by=variable.id))

    assert sorted(i.key for i in plain) == sorted(i.key for i in split)
    assert ({i.key: i.mean for i in plain}
            == {i.key: i.mean for i in split})


# ── colour ──────────────────────────────────────────────────────────────────

def test_members_sharing_a_value_share_an_ink():
    variable = _line()
    items = _items(_ctx(variable=variable, colour_by=variable.id,
                        assignments={"A01": "C0", "A02": "C0",
                                     "B01": "C1", "B02": "C1"}))
    by_key = {i.key: i.color for i in items}
    assert by_key["CTRL"] != by_key["TREAT"], by_key


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _separation(colours) -> float:
    """The closest two of *colours* are in RGB. Distinct is not distinguishable.

    The first version of this test asserted only that the colours differed,
    which every strategy satisfies — including the one that gave three values
    ``#E56AFB`` and ``#DC81EA``, two strings a reader sees as one colour. A
    mutation swapping the strategy left it green, which is how the strategy
    that was actually *worse* came to be in the code with a comment explaining
    why it was better.
    """
    values = list(colours)
    return min(
        sum((a - b) ** 2 for a, b in zip(_rgb(x), _rgb(y))) ** 0.5
        for i, x in enumerate(values) for y in values[i + 1:])


@pytest.mark.parametrize("n,floor", [(2, 150.0), (3, 150.0), (6, 100.0)])
def test_the_palette_entries_are_far_enough_apart(n, floor):
    """Measured, not assumed. The plate palette is already ordered so that
    consecutive entries are far apart; spreading picks across all 96 of them
    to avoid "adjacent hues" collapses three values to 30 apart."""
    variable = _line(pairs=tuple((f"v{i}", None) for i in range(n)))
    palette = vs.value_colors(variable)

    assert len(set(palette.values())) == n
    assert _separation(palette.values()) >= floor, sorted(palette.values())


def test_the_two_ways_of_having_no_value_are_different_inks():
    """"You filled these inconsistently" and "these were never filled" are
    different mistakes with different fixes, so they are not one grey.

    Measured, not merely compared. This asserted `!=` twenty-five lines below
    `_separation`, whose own docstring says distinct is not distinguishable —
    and the two greys it passed were **23** apart in RGB, less than the 30 the
    workplan records as "one colour to any reader" and a fifth of the floor this
    file holds six real values to. §7.8 face two, in the file that documents it.
    """
    assert vs.MIXED_LABEL != vs.UNASSIGNED_LABEL
    assert _separation([vs.MIXED_COLOR, vs.UNASSIGNED_COLOR]) >= 100, (
        "the two non-values are one grey to a reader"
    )


def test_neither_non_value_ink_is_mistakable_for_a_real_value():
    """The other half: far apart from each other is not enough if either lands
    on top of a value's own colour."""
    variable = _line()
    values = list(vs.value_colors(variable).values())

    for ink, name in ((vs.MIXED_COLOR, "MIXED"), (vs.UNASSIGNED_COLOR, "UNASSIGNED")):
        assert _separation(values + [ink]) >= 60, name


def test_the_variables_tab_and_the_plots_ink_a_value_the_same():
    """One colour vocabulary, asserted across the two modules that draw it.

    The tab had its own implementation — the same palette walked with a stride
    of seven rather than one — so every value but the first was one colour on
    the plate that assigned it and a different colour on the bar chart. Asserted
    on the mapping rather than on "both are non-empty", because two wrong
    mappings are both non-empty.
    """
    from well_viewer.features.home.variables import controller as vc

    variable = _line()

    assert vc._value_colours(variable) == vs.value_colors(variable)


def test_a_member_whose_wells_disagree_takes_the_mixed_ink():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C1",
                            "B01": "C1", "B02": "C1"})
    inks = vs.member_inks(ctx, "bar", variable.id)
    assert inks["CTRL"] == vs.MIXED_COLOR
    assert inks["TREAT"] != vs.MIXED_COLOR


def test_a_member_holding_an_unfilled_well_takes_the_unassigned_ink():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "B01": "C1", "B02": "C1"})
    inks = vs.member_inks(ctx, "bar", variable.id)
    assert inks["CTRL"] == vs.UNASSIGNED_COLOR


def test_no_variable_chosen_leaves_the_plate_colours_alone():
    """``{}`` rather than a dict of defaults, so a caller can tell the two
    apart without comparing every entry."""
    ctx = _ctx()
    assert vs.member_inks(ctx, "bar", "") == {}
    plain = _items(ctx)
    assert len({i.color for i in plain}) == 2


# ── the legend ──────────────────────────────────────────────────────────────

def test_the_legend_lists_only_the_values_on_screen():
    """A legend naming six doses over a figure showing two is a claim about
    what is in front of the reader, and the reader counts the legend."""
    variable = _line(pairs=(("HeLa", None), ("U2OS", None), ("HEK", None)))
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    entries = bar_legend_entries(ctx, _items(ctx))

    assert [label for label, _c in entries] == ["HeLa", "U2OS"]


def test_the_legend_keeps_the_users_value_order():
    variable = _line(pairs=(("U2OS", None), ("HeLa", None)))
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    assert [lab for lab, _c in bar_legend_entries(ctx, _items(ctx))] == [
        "U2OS", "HeLa"]


def test_plate_colour_mode_has_no_legend():
    """The plate *is* the legend, which is the argument for it being default."""
    assert bar_legend_entries(_ctx(), []) == []


# ── split ───────────────────────────────────────────────────────────────────

def test_split_puts_each_band_in_one_contiguous_run():
    """``cluster_centers`` centres a header over the positions it saw, so a
    band split across the axis draws its header in the gap between its halves,
    over bars belonging to something else."""
    variable = _line()
    ctx = _ctx(
        selections=[
            {"name": "a", "wells": ["A01"], "hidden": False},
            {"name": "b", "wells": ["B01"], "hidden": False},
            {"name": "c", "wells": ["A02"], "hidden": False},
            {"name": "d", "wells": ["B02"], "hidden": False},
        ],
        variable=variable, split_by=variable.id,
        assignments={"A01": "C0", "A02": "C0", "B01": "C1", "B02": "C1"})
    items = _items(ctx)

    bands = [i.group_key for i in items]
    assert bands == sorted(bands, key=["HeLa", "U2OS"].index), bands
    centres = dict(cluster_centers(items, clustered_x_positions(items)))
    assert set(centres) == {"HeLa", "U2OS"}


def test_the_bands_come_out_in_the_users_value_order():
    variable = _line(pairs=(("U2OS", None), ("HeLa", None)))
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    items = _items(ctx)
    assert [i.group_key for i in items][0] == "U2OS"


def test_the_non_values_sort_after_every_real_one():
    """A `no value` band wedged between 1 µM and 3 µM would read as a dose."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0"})
    items = _items(ctx)
    assert items[-1].group_key == vs.UNASSIGNED_LABEL
    assert items[0].group_key == "HeLa"


def test_a_band_is_named_rather_than_left_blank():
    """An empty cluster header reads as a header that failed to draw."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id, assignments={})
    assert all(i.group_key == vs.UNASSIGNED_LABEL for i in _items(ctx))


def test_drag_order_survives_inside_a_band():
    """Sorting on the band alone would be a second ordering rule silently
    overriding the user's."""
    variable = _line()
    ctx = _ctx(
        selections=[
            {"name": "a", "wells": ["A01"], "hidden": False},
            {"name": "b", "wells": ["A02"], "hidden": False},
        ],
        variable=variable, split_by=variable.id,
        assignments={"A01": "C0", "A02": "C0"})
    assert [i.key for i in _items(ctx)] == ["a", "b"]


# ── what the split had to give up ───────────────────────────────────────────

def test_replacing_an_existing_band_is_reported():
    """The bar tab already uses bands for timepoints and for wells. Taking
    those headers off a figure the user arranged that way, with nothing saying
    where they went, is the substitution §7 forbids."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    for item in items:
        item.group_key = "t=24"

    _out, notice = decorate_bar_items(ctx, items)

    assert "replaced" in notice and "t=24" in notice


def test_a_figure_with_no_bands_to_lose_says_nothing():
    """Saying it anyway would train the reader to skip the line."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    for item in items:
        item.group_key = ""

    _out, notice = decorate_bar_items(ctx, items)
    assert notice == ""


def test_the_notice_is_cleared_on_a_pass_that_has_nothing_to_say():
    """A notice left from the previous layout is the stale-sentence failure,
    moved off the status bar and onto the figure where it is harder to spot."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0"})
    slice_ = ctx.state.feature_slice("bar")
    slice_.split_notice = "left over from last time"

    ctx.state.plot.split_by_variable = ""
    ctx.state.plot.colour_by_variable = ""
    decorate_bar_items(ctx, _items(ctx))

    assert slice_.split_notice == ""


# ── every branch of the collector is decorated ──────────────────────────────

@pytest.mark.parametrize("pool_reps", [True, False])
def test_both_pooling_modes_are_decorated(pool_reps):
    """The reason the pass is applied once over the finished list: the
    collector builds items in four branches, and a figure where some bars
    honour the control and some do not is worse than one where none does."""
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id, pool_reps=pool_reps,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    assert items
    inks = vs.member_inks(ctx, "bar", variable.id)
    for item in items:
        assert item.color == inks[item.key], item.key


def test_a_plate_with_no_groups_is_decorated_too():
    variable = _line()
    ctx = _ctx(selections=[], variable=variable, colour_by=variable.id,
               assignments={w: "C0" for w in _WELLS})
    ctx.state.selection.selected_wells = set(_WELLS)
    items = _items(ctx)
    if items:
        assert len({i.color for i in items}) == 1


# ── the controls, pressed ───────────────────────────────────────────────────
#
# §7.6: asserting a control is offered is not asserting it works. Both of these
# are combos whose ``currentIndexChanged`` is the whole mechanism, and this
# repository has shipped a control connected to nothing.

@pytest.fixture
def band(qtbot):
    """The two real combos, wired the way ``centre_view`` wires them."""
    from types import SimpleNamespace

    from widgets.labeled_combo import LabeledComboBox

    from well_viewer.core.group_by_controller import (
        DECORATION_CONTROLS, refresh_decoration_combos, set_decoration_variable,
    )

    variable = _line()
    ctx = _ctx(variable=variable)
    emitted = []
    ctx.bus = SimpleNamespace(
        plot_options_changed=SimpleNamespace(emit=emitted.append))

    # ``MockApp``'s ctx carries no widget registry; the controller reaches its
    # combos through one, so the test supplies the same two calls it uses.
    registry = {}
    ctx.widgets = SimpleNamespace(
        register=lambda name, widget: registry.setdefault(name, widget),
        get_or_none=registry.get)

    combos = {}
    for field, name, _label in DECORATION_CONTROLS:
        combo = LabeledComboBox(name)
        qtbot.addWidget(combo)
        ctx.widgets.register(name, combo)
        combo.currentIndexChanged.connect(
            lambda *_a, f=field, c=combo: set_decoration_variable(
                ctx, f, c.currentData() or ""))
        combos[field] = combo
    refresh_decoration_combos(ctx)
    return SimpleNamespace(ctx=ctx, combos=combos, variable=variable,
                           emitted=emitted)


def test_both_combos_list_the_plates_variables(band):
    for combo in band.combos.values():
        data = [combo.itemData(i) for i in range(combo.count())]
        assert data == ["", band.variable.id], data


def test_choosing_a_variable_writes_the_state_and_asks_for_a_redraw(band):
    combo = band.combos["colour_by_variable"]
    combo.setCurrentIndex(combo.findData(band.variable.id))

    assert band.ctx.state.plot.colour_by_variable == band.variable.id
    assert band.emitted, "no redraw was requested, so only this tab would change"


def test_the_split_combo_is_wired_to_its_own_field(band):
    """Two controls differing only in a string is how one of them ends up
    writing the other's state."""
    combo = band.combos["split_by_variable"]
    combo.setCurrentIndex(combo.findData(band.variable.id))

    assert band.ctx.state.plot.split_by_variable == band.variable.id
    assert band.ctx.state.plot.colour_by_variable == ""


def test_a_deleted_variable_is_cleared_from_both_controls(band):
    """Left set, the next redraw decorates by a variable nothing offers."""
    from well_viewer.core.group_by_controller import refresh_decoration_combos

    band.ctx.state.plot.colour_by_variable = band.variable.id
    band.ctx.state.plot.split_by_variable = band.variable.id
    band.ctx.state.selection.variables = sv.VariableSet()

    refresh_decoration_combos(band.ctx)

    assert band.ctx.state.plot.colour_by_variable == ""
    assert band.ctx.state.plot.split_by_variable == ""


def test_an_unknown_field_is_refused_rather_than_set(band):
    from well_viewer.core.group_by_controller import set_decoration_variable

    set_decoration_variable(band.ctx, "group_by_variable", band.variable.id)
    assert band.ctx.state.plot.group_by_variable == "", (
        "the decoration setter wrote a field outside its roster")


def test_resetting_the_variables_clears_both_decorations():
    """Left set, a reset leaves the plot decorated by a variable that no longer
    exists — the same combo-and-plot disagreement the Group by clear exists to
    prevent, in a place nobody would think to look.

    Driven through the reset scope rather than by calling the setter, because
    the defect would be a *missing* line there and only the real scope has it.
    """
    from well_viewer.persistence import reset_scopes

    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id, split_by=variable.id)
    ctx.state.plot.group_by_variable = variable.id

    reset_scopes._clear_sample_variables(ctx)

    assert ctx.state.plot.colour_by_variable == ""
    assert ctx.state.plot.split_by_variable == ""
    assert ctx.state.plot.group_by_variable == ""


def test_the_reset_reads_the_controllers_roster_rather_than_naming_fields():
    """A fourth control added to the roster and not to the reset would survive
    it. The roster is the single list, so it cannot drift."""
    import inspect

    from well_viewer.persistence import reset_scopes

    source = inspect.getsource(reset_scopes._clear_sample_variables)
    assert "DECORATION_CONTROLS" in source
    assert "colour_by_variable" not in source, (
        "the reset names the fields itself instead of reading the roster")


# ── the surfaces that render without a ctx ──────────────────────────────────

def test_a_render_with_no_ctx_is_undecorated_rather_than_a_crash():
    """``render_bar_items`` takes ``ctx=None`` on purpose — the Batch Export
    panel renders these panels without one, which its own docstring says.

    Twenty-six tests went red on this, all of them ``AttributeError`` inside
    the decoration pass. A caller with no ctx has no plot state and therefore
    no setting, and the figure it draws is the one the app drew before these
    controls existed: whole and coherent, not a decorated one with pieces
    missing.
    """
    from well_viewer.features.barplot._annotations import annotate_decorations
    from well_viewer.features.barplot._decorate import (
        bar_legend_entries, decorate_bar_items,
    )

    items = _items(_ctx())
    out, notice = decorate_bar_items(None, items)

    assert out == items and notice == ""
    assert bar_legend_entries(None, items) == []
    annotate_decorations(None, items, [], None, headers=False)


def test_a_ctx_without_a_bar_slice_still_decorates():
    """The notice has nowhere to go; the ink and the bands still apply.

    Losing the whole decoration because one place to park a sentence is absent
    would be the tail wagging the figure.
    """
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    ctx.state.feature_slice = lambda _name: None

    items = _items(ctx)
    assert len({i.color for i in items}) == 2


def _batch_items(ctx, group, *, pool_replicates=True):
    """One batch group's bars, through the real batch collector."""
    from well_viewer.features.barplot.collect import (
        FC_STATE_OFF, collect_bar_items_for_group,
    )
    return collect_bar_items_for_group(
        ctx, group, 0.0, val_col="gfp_mean_intensity", threshold=0.0,
        use_sem=True, per_fov_spread=False, fc_state=FC_STATE_OFF,
        pool_replicates=pool_replicates)


def _group(name="G", members=(), solo=()):
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    return BarGroup(name,
                    members=[ReplicateSet(n, list(w)) for n, w in members],
                    solo_wells=list(solo))


def test_batch_export_bar_figures_are_decorated_too():
    """A batch figure that ignored the control would disagree with the tab it
    mirrors, on a plate the user set up once.

    Asserted against the **variable's own palette**, not against "the colours
    differ". The batch collector already gives each member a different ink from
    its own palette, so a distinctness check passes with the decoration deleted
    — which is exactly what the first version of this test did, and a mutation
    removing the whole call left all of it green.
    """
    variable = _line()
    # The values are assigned **against** the member order on purpose. The batch
    # collector inks its members from the same ``WELL_COLORS`` in member order,
    # so with CTRL holding the first value the decorated and undecorated inks
    # are byte-identical and nothing can tell them apart — which is how three
    # mutations, including deleting the decoration outright, left the first
    # version of these tests green.
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])
    palette = vs.value_colors(variable)

    by_key = {i.key: i.color for i in _batch_items(ctx, group)}

    assert by_key == {"CTRL": palette["C1"], "TREAT": palette["C0"]}, by_key


def test_the_batch_keys_are_the_groups_own_not_the_tabs():
    """The reason the key space is passed rather than looked up.

    This group's members are named `CTRL` and `TREAT`; the tab's members here
    are the *wells*, because no selections are defined. Matching the batch
    items against the tab's key space would colour nothing at all — silently,
    since a key that resolves to no member simply keeps the colour it had.
    """
    from well_viewer.domain.variable_series import member_inks

    variable = _line()
    ctx = _ctx(selections=[], variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    from well_viewer.domain.variable_series import member_inks

    tab_keys = set(member_inks(ctx, "bar", variable.id))
    assert "CTRL" not in tab_keys, (
        "the fixture no longer distinguishes the two key spaces")

    palette = vs.value_colors(variable)
    by_key = {i.key: i.color for i in _batch_items(ctx, group)}

    assert by_key == {"CTRL": palette["C1"], "TREAT": palette["C0"]}, (
        "the batch bars were matched against the tab's keys, so they kept "
        f"whatever ink the batch palette gave them: {by_key}")


def test_the_batch_and_the_tab_agree_on_what_a_value_looks_like():
    """One definition of "what colour is HeLa", not two."""

    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    palette = vs.value_colors(variable)
    tab = {i.key: i.color for i in _items(ctx)}
    batch = {i.key: i.color for i in _batch_items(ctx, group)}

    assert tab == batch, (tab, batch)
    # Against the palette too, so the pair cannot agree by both being wrong.
    assert set(batch.values()) == {palette["C0"], palette["C1"]}


def test_the_batch_expanded_mode_is_keyed_on_wells_and_still_decorated():
    """With pooling off the batch keys become well tokens. The map is built
    from the same ``members`` list either way, so it follows the flip."""
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    palette = vs.value_colors(variable)
    by_key = {i.key: i.color for i in
              _batch_items(ctx, group, pool_replicates=False)}

    assert set(by_key) == {"A01", "A02", "B01", "B02"}
    assert by_key == {"A01": palette["C1"], "A02": palette["C1"],
                      "B01": palette["C0"], "B02": palette["C0"]}, by_key


def test_a_batch_solo_well_is_decorated_by_its_own_value():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "B01": "C0"})
    group = _group(solo=["A01", "B01"])
    palette = vs.value_colors(variable)

    by_key = {i.key: i.color for i in _batch_items(ctx, group)}
    assert by_key == {"A01": palette["C1"], "B01": palette["C0"]}, by_key


def test_grey_bars_get_no_colour_by_legend(monkeypatch):
    """A legend over bars that are not carrying the variable's inks is a key to
    a colouring the figure does not have.

    ``redraw_bars`` passes ``mono_bar_color`` for every bar in grey mode, which
    discards the ``item.color`` ``decorate_bar_items`` just set — so the figure
    is uniformly grey and the legend was still drawn, offering a reader swatches
    to map onto bars that carry none. Worse than no legend, because it is
    confidently wrong.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from well_viewer.features.barplot import _annotations as ann

    drawn: list = []
    monkeypatch.setattr(ann, "bar_legend_entries",
                        lambda ctx, items: drawn.append(items) or [("HeLa", "#112233")])

    fig, ax = plt.subplots()
    try:
        for mode, expect_legend in (("plate", True), ("grey", False)):
            ax.clear()
            drawn.clear()
            ctx = _ctx_with_mode(mode)
            ann.annotate_colour_legend(ctx, ax)
            assert bool(ax.get_legend()) is expect_legend, mode
    finally:
        plt.close(fig)


def _ctx_with_mode(mode):
    from types import SimpleNamespace

    from well_viewer.core.app_state import AppState

    state = AppState()
    state.plot.bar_color_mode = mode
    state.plot.colour_by_variable = "dose"
    return SimpleNamespace(state=state)
