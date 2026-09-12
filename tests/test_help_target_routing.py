"""The help drawer aims at the page the user is actually on, in all three modes.

``AllWellApp._help_section_for_current_page`` picks the manual heading the drawer
scrolls to. It used to recognise one pane other than Review — Analyze, by a bare
``currentIndex() == 1`` on the central pane stack — so opening the drawer from
Datasets (index 2) fell through to the Review branch and landed on whichever
Review tab had last been visited.

Tested against stubs rather than a live ``AllWellApp``: the methods take the
panes' answers and turn them into a heading, and that translation is the whole
contract. What the panes themselves report is tested where those panes live.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from ui import help_topics
from allwell import AllWellApp


def _shell(mode: str, *, analyze=None, datasets=None, leaf: str | None = None):
    """A stub app in *mode*, with whichever panes the branch under test reads."""
    review = types.SimpleNamespace(currentMode=lambda: mode, _ctx=None)
    if leaf is not None:
        # The Review branch resolves its leaf through tab_controller; give it a
        # ctx so it gets that far, and patch the resolution per-test.
        review._ctx = object()
    shell = types.SimpleNamespace(_review=review, _analyze=analyze, _datasets=datasets)
    for name in ("_help_section_for_current_page", "_analyze_help_section",
                 "_datasets_help_section", "_review_help_section"):
        setattr(shell, name, types.MethodType(getattr(AllWellApp, name), shell))
    return shell


# ── Datasets ─────────────────────────────────────────────────────────────────

def test_datasets_lands_on_the_tool_showing_now():
    pane = types.SimpleNamespace(current_help_page=lambda: "Merge Plates")
    shell = _shell("datasets", datasets=pane)
    assert shell._help_section_for_current_page() == "Merge Plates"


def test_datasets_maps_a_rail_label_that_differs_from_its_heading():
    """The rail says "Validate & Repair"; the manual says "…Dataset"."""
    pane = types.SimpleNamespace(current_help_page=lambda: "Validate & Repair")
    shell = _shell("datasets", datasets=pane)
    assert shell._help_section_for_current_page() == "Validate & Repair Dataset"


@pytest.mark.parametrize("pane", [
    None,                                                   # not built yet
    types.SimpleNamespace(current_help_page=lambda: ""),     # nothing selected
    types.SimpleNamespace(current_help_page=lambda: "Nonesuch"),
])
def test_datasets_falls_back_to_its_own_overview(pane):
    """Never to a Review heading — that was the reported behaviour."""
    shell = _shell("datasets", datasets=pane)
    assert shell._help_section_for_current_page() == help_topics.MODE_OVERVIEW["datasets"]


# ── Analyze ──────────────────────────────────────────────────────────────────

def test_analyze_lands_on_the_scrolled_to_section():
    pane = types.SimpleNamespace(current_help_section=lambda: "Scratch Disk")
    shell = _shell("analyze", analyze=pane)
    assert shell._help_section_for_current_page() == "10. Scratch disk"


@pytest.mark.parametrize("pane", [
    None,
    types.SimpleNamespace(current_help_section=lambda: ""),
    types.SimpleNamespace(current_help_section=lambda: "Nonesuch"),
])
def test_analyze_falls_back_to_its_own_overview(pane):
    shell = _shell("analyze", analyze=pane)
    assert shell._help_section_for_current_page() == help_topics.MODE_OVERVIEW["analyze"]


# ── Review ───────────────────────────────────────────────────────────────────

def test_review_still_resolves_its_leaf(monkeypatch):
    import review.core.tab_controller as tc
    monkeypatch.setattr(tc, "current_centre_tab", lambda _ctx: "Heat Map")
    shell = _shell("review", leaf="Heat Map")
    assert shell._help_section_for_current_page() == "Heat Map"


def test_review_loose_match_survives_a_prefixed_leaf(monkeypatch):
    import review.core.tab_controller as tc
    monkeypatch.setattr(tc, "current_centre_tab", lambda _ctx: "Bar Plots (split)")
    shell = _shell("review", leaf="Bar Plots (split)")
    assert shell._help_section_for_current_page() == "Bar Plots"


def test_review_unknown_leaf_lands_on_the_review_overview(monkeypatch):
    import review.core.tab_controller as tc
    monkeypatch.setattr(tc, "current_centre_tab", lambda _ctx: "Nonesuch")
    shell = _shell("review", leaf="Nonesuch")
    assert shell._help_section_for_current_page() == help_topics.MODE_OVERVIEW["review"]


def test_review_without_a_ctx_lands_on_the_review_overview():
    """Same rule as the other two panes: a pane that cannot name a page falls
    back to its own mode's overview. Returning "" here would leave the drawer at
    the top of the manual, which reads as a failed scroll rather than a choice."""
    shell = _shell("review")                       # no ctx built yet
    assert shell._help_section_for_current_page() == help_topics.MODE_OVERVIEW["review"]


def test_review_lands_on_the_overview_when_the_leaf_lookup_raises(monkeypatch):
    import review.core.tab_controller as tc

    def _boom(_ctx):
        raise RuntimeError("tab controller not ready")

    monkeypatch.setattr(tc, "current_centre_tab", _boom)
    shell = _shell("review", leaf="anything")
    assert shell._help_section_for_current_page() == help_topics.MODE_OVERVIEW["review"]


def test_no_review_pane_yields_no_target():
    shell = types.SimpleNamespace(_review=None)
    shell._help_section_for_current_page = types.MethodType(
        AllWellApp._help_section_for_current_page, shell)
    assert shell._help_section_for_current_page() == ""
