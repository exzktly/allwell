"""Grouping by a variable drops wells, and every figure has to say so.

``plot_replicate_sets`` substitutes the member set for **every** view at once —
bars, lines, statistics, heat map, scatter, the exports — and it drops every well
with no value for the chosen variable. The numbers move, the *n* moves, and a
figure that quietly lost eight wells is indistinguishable from a figure of a
plate that had eighty-eight. §7's cardinal case.

The sentence naming those wells existed and reached exactly one tab's title. Two
reviewers found it independently; a third confirmed by mutation that even that
one surface was untested. It is now drawn from
``apply_export_style_to_current`` — the call every renderer already ends with,
thirty-nine sites and a convention that keeps it that way — so it cannot miss a
tab and a tab added later inherits it.

Two properties matter as much as the sentence:

* it is **removed** on a redraw that no longer needs it, or it becomes the
  outlived-status-bar defect one layer up, and
* it tells the truth about the *fallback*. When the chosen variable groups
  nothing, ``plot_replicate_sets`` keeps the user's own sets rather than
  emptying every plot in the app — so the wells with no value are on the figure,
  and the old sentence's "are not plotted" contradicted the picture it was
  printed on.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from well_viewer.core.group_by_controller import group_by_shortfall  # noqa: E402
from well_viewer.core.plot_style import _NOTICE_TAG, _apply_grouping_notice  # noqa: E402
from well_viewer.domain import sample_variables as sv  # noqa: E402


class _Ctx:
    """Real state slices, stub only where the boundary is.

    ``VariableSet`` and the two plot fields are the shipped objects, so a
    renamed attribute fails here rather than being invented by the fixture —
    §7.8's first face is what a hand-built namespace cannot catch.
    """

    def __init__(self, variables, assignments, wells, group_by=""):
        from well_viewer.core.app_state import AppState

        self.state = AppState()
        self.state.dataset.well_paths = {w: f"/tmp/{w}.csv" for w in wells}
        self.state.plot.group_by_variable = group_by
        vset = sv.from_block({
            "schema_version": 1,
            "variables": variables,
            "assignments": assignments,
        })
        self.state.selection.variables = vset


def _plate(group_by="dose"):
    """Four wells, two of which carry a dose. The two that do not are the ones
    the notice has to name — and they are a *minority*, so a notice that named
    every well would read plausibly and still be wrong."""
    variables = [{
        "id": "dose", "name": "Dose", "kind": "number", "unit": "µM",
        "values": [{"id": "v1", "label": "1"}, {"id": "v2", "label": "10"}],
    }]
    assignments = {"dose": {"A01": "v1", "A02": "v2"}}
    return _Ctx(variables, assignments, ["A01", "A02", "A03", "A04"], group_by)


def _notices(fig) -> list[str]:
    return [t.get_text() for t in fig.texts if getattr(t, _NOTICE_TAG, False)]


def test_the_notice_names_the_wells_the_grouping_left_out():
    ctx = _plate()
    fig = plt.figure()
    try:
        _apply_grouping_notice(ctx, fig)

        assert _notices(fig) == [
            "Grouped by Dose: 2 well(s) have no value and are not plotted — A03, A04."
        ]
    finally:
        plt.close(fig)


def test_no_grouping_draws_no_notice():
    ctx = _plate(group_by="")
    fig = plt.figure()
    try:
        _apply_grouping_notice(ctx, fig)
        assert _notices(fig) == []
    finally:
        plt.close(fig)


def test_a_fully_assigned_plate_draws_no_notice():
    ctx = _Ctx(
        [{"id": "dose", "name": "Dose", "kind": "number",
          "values": [{"id": "v1", "label": "1"}]}],
        {"dose": {"A01": "v1", "A02": "v1"}},
        ["A01", "A02"], "dose",
    )
    fig = plt.figure()
    try:
        _apply_grouping_notice(ctx, fig)
        assert _notices(fig) == []
    finally:
        plt.close(fig)


def test_the_notice_is_replaced_rather_than_stacked():
    """A redraw calls this again on the same figure. Appending would leave two
    sentences on top of each other, and — worse — a second redraw after the gap
    is filled would leave the first one saying wells are missing that are not."""
    ctx = _plate()
    fig = plt.figure()
    try:
        _apply_grouping_notice(ctx, fig)
        _apply_grouping_notice(ctx, fig)
        assert len(_notices(fig)) == 1

        # The gap is filled; the next redraw must clear the sentence.
        ctx.state.selection.variables = sv.assign(
            ctx.state.selection.variables, "dose", ["A03", "A04"], "v1")
        _apply_grouping_notice(ctx, fig)
        assert _notices(fig) == []
    finally:
        plt.close(fig)


def test_a_grouping_that_matched_nothing_says_that_and_not_not_plotted():
    """The fallback, stated. ``plot_replicate_sets`` keeps the user's own sets
    when the variable groups nothing, so those wells *are* drawn — the previous
    sentence claimed the opposite on a figure that showed them."""
    ctx = _Ctx(
        [{"id": "dose", "name": "Dose", "kind": "number",
          "values": [{"id": "v1", "label": "1"}]}],
        {},                                   # nothing assigned anywhere
        ["A01", "A02"], "dose",
    )

    message = group_by_shortfall(ctx)

    assert message == "Group by Dose matched no wells — showing your own groups instead."
    assert "not plotted" not in message


def test_a_deleted_variable_says_nothing_rather_than_guessing():
    ctx = _plate(group_by="a-variable-that-was-deleted")
    assert group_by_shortfall(ctx) == ""


# ── through the real app ─────────────────────────────────────────────────────

def test_a_real_bar_figure_carries_the_notice(loaded_app):
    """The claim the whole design rests on: drawn from the call every renderer
    ends with, the notice cannot miss a tab. Everything above exercises the
    helper; this drives an actual Bar redraw on a real app and reads the sentence
    off the figure it produced.

    The Bar tab specifically, because it is the tab F's own acceptance
    walkthrough names and the one whose title carried nothing before.
    """
    import well_viewer.domain.sample_variables as _sv
    from well_viewer.persistence import view_state

    app = loaded_app
    ctx = app._ctx
    wells = sorted(ctx.state.dataset.well_paths or {})
    assert len(wells) >= 3, "need a plate with a well to leave out"

    vset = _sv.from_block({
        "schema_version": 1,
        "variables": [{
            "id": "dose", "name": "Dose", "kind": "number",
            "values": [{"id": "v1", "label": "1"}, {"id": "v2", "label": "10"}],
        }],
        "assignments": {},
    })
    # Every well but the last, so the grouping applies *and* leaves one out —
    # the case where both halves of the sentence have to be right.
    vset = _sv.assign(vset, "dose", wells[:-1], "v1")
    ctx.state.selection.variables = vset
    ctx.state.plot.group_by_variable = "dose"

    view_state._activate_tab(ctx, "Bar Plots")
    app._rebuild_all()

    fig = getattr(app, "_bar_fig", None)
    assert fig is not None, "the Bar tab drew no figure to carry a notice"
    notices = [t.get_text() for t in fig.texts if getattr(t, _NOTICE_TAG, False)]
    assert notices, (
        "a real Bar figure grouped by a partly-assigned variable carries no "
        "notice — the wells it dropped are unaccounted for on the artifact"
    )
    assert wells[-1] in notices[0], notices
