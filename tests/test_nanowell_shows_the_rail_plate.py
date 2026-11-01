"""Opening the Nanowells page shows the rail plate.

The tab switch (``review/core/tab_controller.py``) hides every rail frame, then
shows the ones the page declares (``rail_sections``, through
``show_rail_sections``) and runs its ``on_activate`` hook. When each hook showed
its own frames, Nanowells' was the bare ``nw_on_activate``, which never did, so
the page opened with no plate to pick its well from. This drives the switch's
two steps for the registered descriptor, with the frame hidden as the switch
leaves it.
"""

from __future__ import annotations

from types import SimpleNamespace

import review.features.nanowell.controller as nw_controller
from review.features import registry


class _Frame:
    def __init__(self) -> None:
        self.visible = False

    def setVisible(self, on: bool) -> None:
        self.visible = bool(on)


def test_activating_nanowells_shows_the_rail_plate(monkeypatch):
    frame = _Frame()
    refreshed: list[bool] = []
    activated: list[bool] = []
    monkeypatch.setattr(nw_controller, "nw_on_activate", lambda _ctx: activated.append(True))
    from review.core.tab_controller import show_rail_sections
    ctx = SimpleNamespace(
        widgets=SimpleNamespace(
            get_or_none=lambda name: frame if name == "_sidebar_main_frame" else None),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: refreshed.append(True)),
    )

    desc = registry.build_feature_registry().get("Nanowells")
    show_rail_sections(ctx, desc)
    desc.on_activate(ctx)

    assert frame.visible is True, "the rail plate stays hidden on the Nanowells page"
    assert refreshed == [True], "the plate is shown without being repainted for this page"
    assert activated == [True], "the page's own activation no longer runs"
