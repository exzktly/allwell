"""A tab whose saved state was just applied is repainted.

``on_tab_change`` draws the tab, *then* drains any restored view-state slated
for it — that order is deliberate, since the apply step validates against combo
items the draw has populated. It leaves the figure one step behind: it was
drawn from pre-restore defaults, and the restored state has to reach it through
a repaint that the restore itself does not perform.

That repaint was a hand-written ``if/elif`` over three tab names, and six tabs
own a view-state block. **Response Curves was one of the three that were
missing**, which is what a user saw: after a launch the curves drew as one
undivided set while the Series button beside them named the split, and toggling
a series off and on — the first thing that reaches the redraw — fixed it. The
figure and the control disagreed with nothing on screen to say which was live.

So the rule is stated against the registry: every tab that owns a block is
repainted after its block is applied, or it is exempt here with the reason
written down.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.core import tab_controller
from well_viewer.features.registry import build_feature_registry
from well_viewer.persistence import view_state

#: Tabs that own a block and are deliberately **not** repainted, with why.
_NOT_REPAINTED = {
    "Image Table": (
        "It renders images off disk and only when the user presses Generate — "
        "a restored grid can be hundreds of frames and paying for them is the "
        "user's decision. The restore marks it stale instead, so the shelf "
        "header asks for the Generate rather than taking it."
    ),
}


def _tabs_that_own_a_block() -> list[str]:
    registry = build_feature_registry()
    names = list(view_state._BUILTIN_TAB_BLOCKS)
    names += [d.name for d in registry.all() if d.view_state_io]
    return sorted(set(names))


def _ctx_recording_redraws(redraws: list[str]):
    """A ctx whose every repaint route records the name it was asked for."""
    features = SimpleNamespace(
        redraw=lambda _ctx, name: (redraws.append(name), True)[1],
    )
    return SimpleNamespace(features=features, window=SimpleNamespace())


def test_the_registry_names_the_tabs_this_guard_measures():
    """A lookup that came back short would pass every check below in silence."""
    owners = _tabs_that_own_a_block()
    assert len(owners) >= 6, owners
    assert "Response Curves" in owners and "Bar Plots" in owners


@pytest.mark.parametrize("tab", [t for t in _tabs_that_own_a_block()
                                 if t not in _NOT_REPAINTED])
def test_every_tab_that_owns_a_block_is_repainted_after_its_restore(
        tab, monkeypatch):
    """Not "something was repainted" — *this* tab was.

    A fan-out that repainted the Line tab for every restore would satisfy a
    looser assertion perfectly, and that is close to what the missing branches
    did: nothing at all, while the tab's own controls moved.
    """
    redraws: list[str] = []
    monkeypatch.setattr(tab_controller, "redraw_app",
                        lambda _ctx: redraws.append("redraw_app"))
    tab_controller.redraw_after_restore(_ctx_recording_redraws(redraws), tab)
    assert redraws, f"restoring {tab!r} repainted nothing"
    assert tab in redraws or "redraw_app" in redraws, (
        f"restoring {tab!r} repainted {redraws} — not the tab it restored")


def test_the_exempt_tab_is_not_repainted(monkeypatch):
    redraws: list[str] = []
    monkeypatch.setattr(tab_controller, "redraw_app",
                        lambda _ctx: redraws.append("redraw_app"))
    tab_controller.redraw_after_restore(_ctx_recording_redraws(redraws),
                                        "Image Table")
    assert redraws == [], (
        "the Image Table was repainted, which reads hundreds of frames off "
        f"disk the user did not ask for: {redraws}")


def test_the_exemption_still_names_a_tab_that_owns_a_block():
    """An exemption for a tab that keeps nothing is a stale claim."""
    stale = sorted(set(_NOT_REPAINTED) - set(_tabs_that_own_a_block()))
    assert not stale, f"an exemption names a tab that owns no block: {stale}"


def test_a_tab_with_no_block_is_never_dispatched_to(monkeypatch):
    """Line Graphs' registry redraw takes required keyword arguments, so a
    blanket ``features.redraw`` on it would raise rather than repaint. It owns
    no block, so the restore can never reach it — asserted rather than assumed,
    because the day it gains one this has to be looked at again."""
    assert "Line Graphs" not in _tabs_that_own_a_block()
