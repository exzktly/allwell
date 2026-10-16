"""A click on the sidebar plate changes what the plots draw — in every grouping.

Reported as: with replicate groups and sample variables both set, clicking a
well on a plotting tab did nothing to the plot. Two defects produced it, and
each was a click that wrote state no plot read:

* **A well in no group.** With groups defined the plate is passive and a click
  on an ungrouped well adds it to ``active_solo_wells``. The plate lit it, the
  Scatter and Distribution tabs drew it, and Bar Plots and Line Graphs did not:
  their member lists were the visible groups and nothing else.
* **Grouped by a variable.** The plots draw one member per value of the
  Group-by variable, but a click still toggled the clicked well's *replicate
  group* ``hidden`` flag, which a variable grouping never reads. The plate
  dimmed the group and no plot changed. With no groups defined the plate was
  not passive at all, so a click selected a well the plots also ignore.

Every test here drives the real app — the plate's own ``wellActivated``
signal, wired by ``sidebar_view`` — and asserts on what the Bar and Line
collectors emit, not on the state the click wrote: the bug was precisely that
the state was written and the plots did not read it.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _on_bar_plots(app):
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Bar Plots")
    assert app._current_centre_tab() == "Bar Plots"
    app._refresh_sidebar_map_now()


def _drawn(app):
    """``(bar keys, line keys)`` as the two collectors emit them."""
    from review.features.barplot.collect import collect_bar_items
    from review.features.lineplot.controller import collect_line_series

    ctx = app._ctx
    _, items, _ = collect_bar_items(ctx, [0.0])
    series, _ = collect_line_series(ctx, threshold=0.0, use_sem=True)
    return sorted({i.key for i in items}), sorted({s.key for s in series})


def _click(app, well):
    app._sidebar_plate.wellActivated.emit(well)
    app._refresh_sidebar_map_now()


def _dose(app, assign):
    """Define a two-value ``Dose`` variable with *assign* = ``{well: "1"|"10"}``."""
    from review.domain import sample_variables as sv
    variable = sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="",
        values=(sv.VariableValue("d1", "1", 1.0), sv.VariableValue("d10", "10", 10.0)))
    ids = {"1": "d1", "10": "d10"}
    app._ctx.state.selection.variables = sv.VariableSet(
        variables=(variable,),
        assignments={"dose": {w: ids[v] for w, v in assign.items()}})


def _group_by_dose(app):
    from review.core.group_by_controller import set_group_by_variable
    set_group_by_variable(app._ctx, "dose")
    app._refresh_sidebar_map_now()


def test_clicking_an_ungrouped_well_adds_it_to_bar_and_line(loaded_app):
    from review.core.selection_controller import sel_add

    app = loaded_app
    sel_add(app._ctx, "G1", ["A01", "A02"], make_current=False)
    _on_bar_plots(app)
    assert app._sidebar_plate._grid.mode() == "passive"
    assert _drawn(app) == (["G1"], ["G1"])

    _click(app, "B01")
    assert _drawn(app) == (["B01", "G1"], ["B01", "G1"]), (
        "a well in no group was clicked onto the plate and neither the Bar nor "
        "the Line plot drew it")
    assert set(app._sidebar_plate.selectedWellIds()) == {"A01", "A02", "B01"}

    _click(app, "B01")
    assert _drawn(app) == (["G1"], ["G1"])


def test_a_solo_well_is_drawn_when_every_group_is_hidden(loaded_app):
    """The bar keys used to be ``[]`` whenever every group was hidden, so a solo
    well could not be plotted on its own at all."""
    from review.core.selection_controller import sel_add

    app = loaded_app
    sel_add(app._ctx, "G1", ["A01", "A02"], make_current=False)
    _on_bar_plots(app)
    _click(app, "A01")          # hides G1
    _click(app, "B02")          # solo
    assert _drawn(app) == (["B02"], ["B02"])


def test_grouped_by_a_variable_a_click_hides_and_shows_that_value(loaded_app):
    from review.core.selection_controller import sel_add

    app = loaded_app
    # The groups cut *across* the doses, so toggling a group (the old
    # behaviour) and toggling a value cannot produce the same picture.
    sel_add(app._ctx, "G1", ["A01", "B01"], make_current=False)
    sel_add(app._ctx, "G2", ["A02", "B02"], make_current=False)
    _dose(app, {"A01": "1", "A02": "1", "B01": "10", "B02": "10"})
    _on_bar_plots(app)
    _group_by_dose(app)
    assert _drawn(app) == (["1", "10"], ["1", "10"])

    _click(app, "A01")
    assert _drawn(app) == (["10"], ["10"]), (
        "a click under Group by Dose must hide the clicked well's dose")
    assert set(app._sidebar_plate.selectedWellIds()) == {"B01", "B02"}
    # The user's groups are untouched — they are not what the plot draws.
    assert [s.get("hidden") for s in app._ctx.state.selection.selections] == [False, False]

    _click(app, "A02")          # same dose: shows it again
    assert _drawn(app) == (["1", "10"], ["1", "10"])


def test_grouped_by_a_variable_without_groups_the_plate_toggles_values(loaded_app):
    app = loaded_app
    _dose(app, {"A01": "1", "A02": "1", "B01": "10", "B02": "10"})
    _on_bar_plots(app)
    _group_by_dose(app)
    assert app._sidebar_plate._grid.mode() == "passive", (
        "with the plots pooled by a variable a well selection is ignored, so "
        "the plate must not offer one")

    _click(app, "B02")
    assert _drawn(app) == (["1"], ["1"])


def test_hiding_every_value_empties_the_plots_rather_than_falling_back(loaded_app):
    from review.core.selection_controller import sel_add

    app = loaded_app
    sel_add(app._ctx, "G1", ["A01", "A02"], make_current=False)
    _dose(app, {"A01": "1", "A02": "1", "B01": "10", "B02": "10"})
    _on_bar_plots(app)
    _group_by_dose(app)
    _click(app, "A01")
    _click(app, "B01")
    assert _drawn(app) == ([], [])


def test_a_well_with_no_value_says_why_a_click_changed_nothing(loaded_app):
    app = loaded_app
    _dose(app, {"A01": "1", "A02": "1", "B01": "10"})   # B02 has no dose
    _on_bar_plots(app)
    _group_by_dose(app)
    shown = []
    app._ctx.notify.set_status = lambda text, *a, **k: shown.append(text)

    _click(app, "B02")
    assert _drawn(app) == (["1", "10"], ["1", "10"])
    assert shown and shown[-1].startswith("B02 has no Dose value"), shown


def test_changing_the_grouping_forgets_the_hidden_values(loaded_app):
    from review.core.group_by_controller import set_group_by_variable

    app = loaded_app
    _dose(app, {"A01": "1", "A02": "1", "B01": "10", "B02": "10"})
    _on_bar_plots(app)
    _group_by_dose(app)
    _click(app, "A01")
    assert app._ctx.state.plot.group_by_hidden_values == {"d1"}
    set_group_by_variable(app._ctx, "")
    assert app._ctx.state.plot.group_by_hidden_values == set()
