"""The selected comparison group is visible, and the results say what is owed.

The editor's own instruction is "select a group below, then drag wells on the
map to assign them to it". Which row is armed is therefore the difference
between a drag landing where the user meant it and landing somewhere else — and
the ``state="selected"`` property was being set on every pass with **no style
rule for ``#StatsGroupCard`` anywhere in the repo**, so it painted nothing. A
property nothing reads is not a selected state; it is a comment.

The results placeholder had the mirror-image fault: fixed at "define two
comparison groups above", it went on saying so with both groups listed twenty
pixels above it.

Both are asserted against what the widget would actually be drawn with, not
against the property or the roster — a stylesheet that mentions the object name
and a property that is set are each satisfied by the broken version.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


def _cards(ctx):
    from PySide6.QtWidgets import QFrame
    inner = ctx.widgets.get("_stats_grp_inner")
    return [w for w in inner.findChildren(QFrame)
            if w.objectName() == "StatsGroupCard"]


def _two_groups(ctx):
    from review.features.stats.controller import (
        stats_grp_add, stats_refresh_group_list,
    )
    wells = list(ctx.state.dataset.well_paths)
    ctx.state.stats.clear_groups()
    for name, chunk in (("Control", wells[:3]), ("Treated", wells[3:6])):
        group = stats_grp_add(ctx) or ctx.state.stats.active_group()
        group.name = name
        group.solo_wells.extend(chunk)
    stats_refresh_group_list(ctx)


def test_the_stylesheet_paints_the_selected_card(qapp_style):
    """The rule exists and distinguishes the two states.

    Reads the application stylesheet rather than the theme module's source, so a
    rule written but never installed fails here. It cannot know the two states
    *look* different without rendering, so it asserts the selected selector
    carries declarations the unselected one does not.
    """
    base, selected = qapp_style
    assert base, "no rule for QFrame#StatsGroupCard is installed at all"
    assert selected, (
        'QFrame#StatsGroupCard[state="selected"] has no rule, so the property '
        "the controller sets on every pass paints nothing")
    assert selected != base, (
        "the selected rule is identical to the unselected one, so an armed "
        "group is indistinguishable from the rest")


@pytest.fixture
def qapp_style():
    """The two StatsGroupCard rules as the app would install them.

    From ``theme.qss()`` — the string the app actually sets on the
    QApplication — rather than from the module's source text, so a rule written
    into the file but never reaching the stylesheet fails here.
    """
    import re

    from ui import theme
    qss = theme.qss()

    def _rule(selector: str) -> str:
        pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
        found = re.search(pattern, qss)
        return found.group(1).strip() if found else ""

    return (_rule("QFrame#StatsGroupCard"),
            _rule('QFrame#StatsGroupCard[state="selected"]'))


@pytest.mark.slow
def test_exactly_one_card_carries_the_selected_property(loaded_app):
    """And it is the group the plate's drag will actually write into."""
    app = loaded_app
    ctx = app._ctx
    app._notebook.setCurrentByName("Statistics")
    _two_groups(ctx)

    cards = _cards(ctx)
    assert len(cards) == 2
    armed = [c for c in cards if c.property("state") == "selected"]
    assert len(armed) == 1, f"{len(armed)} cards marked selected, expected one"
    assert cards.index(armed[0]) == ctx.state.stats.active_grp, (
        "the card drawn as selected is not the group a drag would fill")


@pytest.mark.slow
def test_the_results_placeholder_says_what_is_still_owed(loaded_app):
    """Three states, three sentences — asserted as the sentences, because "it
    mentions groups" is true of the fixed text in every one of them."""
    from review.features.stats.controller import (
        stats_grp_add, stats_grp_clear_all, stats_refresh_group_list,
    )

    app = loaded_app
    ctx = app._ctx
    app._notebook.setCurrentByName("Statistics")
    box = ctx.widgets.get("_stats_result_text")

    stats_grp_clear_all(ctx)
    assert box.placeholderText().startswith("Define two comparison groups")

    stats_grp_add(ctx)
    stats_refresh_group_list(ctx)
    assert box.placeholderText().startswith("One comparison group so far"), (
        f"with one group defined the panel says {box.placeholderText()!r}")

    stats_grp_add(ctx)
    stats_refresh_group_list(ctx)
    assert box.placeholderText().startswith("Choose a test above"), (
        f"with both groups defined the panel still asks for them: "
        f"{box.placeholderText()!r}")


@pytest.mark.slow
def test_the_header_count_reports_the_empty_state_too(loaded_app):
    """A folded section that says nothing is one you have to unfold to find out
    whether you need it unfolded — and that is most true at zero groups, which
    is where the first version said nothing at all.

    Asserts the three strings, not "the label changed": a count that read "0
    groups" and one that read "" both change, and only one of them tells a user
    with the editor folded that there is nothing in it.
    """
    from review.features.stats.controller import (
        stats_grp_add, stats_grp_clear_all, stats_refresh_group_list,
    )

    app = loaded_app
    ctx = app._ctx
    app._notebook.setCurrentByName("Statistics")
    label = ctx.widgets.get("_stats_group_count_lbl")

    stats_grp_clear_all(ctx)
    assert label.text() == "None yet", (
        f"with no groups the folded header says {label.text()!r}")

    stats_grp_add(ctx)
    stats_refresh_group_list(ctx)
    assert label.text() == "1 group"

    stats_grp_add(ctx)
    stats_refresh_group_list(ctx)
    assert label.text() == "2 groups"


@pytest.mark.slow
def test_clearing_the_groups_drops_the_floors_they_justified(loaded_app):
    """Both floors are measured from a card the pass actually built, so a pass
    that builds none has no basis for either.

    Left in place they outlive their reason: after Clear All, a one-line "No
    groups." label sat in a box still reserving two card rows and a card's
    width — and that width is what the responsive row measures when deciding
    whether the map fits beside it, so a stale one moves the flip point for a
    list that is no longer there.
    """
    from review.features.stats.controller import (
        stats_grp_clear_all, stats_refresh_group_list,
    )

    app = loaded_app
    ctx = app._ctx
    app._notebook.setCurrentByName("Statistics")
    _two_groups(ctx)
    canvas = ctx.widgets.get("_stats_grp_canvas")
    assert canvas.minimumWidth() > 0 and canvas.minimumHeight() > 0, (
        "the floors were never set with two groups defined, so this test "
        "cannot show them being dropped")

    stats_grp_clear_all(ctx)
    stats_refresh_group_list(ctx)
    assert canvas.minimumWidth() == 0, (
        f"the list still reserves {canvas.minimumWidth()}px of width for cards "
        f"it no longer holds")
    assert canvas.minimumHeight() == 0, (
        f"the list still reserves {canvas.minimumHeight()}px of height for two "
        f"cards it no longer holds")
