"""Opening the Nanowells page shows the rail plate.

The tab switch (``review/core/tab_controller.py``) hides every sidebar section
and then lets the page's ``on_activate`` hook show the ones it needs. Every
per-well page's hook puts ``_sidebar_main_frame`` — the rail plate — back;
Nanowells' hook was the bare ``nw_on_activate``, which never did, so the page
opened with no plate to pick its well from. This drives the registered hook,
the one the tab switch calls, with the frame hidden as the switch leaves it.
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
    ctx = SimpleNamespace(
        widgets=SimpleNamespace(get=lambda name: frame if name == "_sidebar_main_frame" else None),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: refreshed.append(True)),
    )

    desc = registry.build_feature_registry().get("Nanowells")
    desc.on_activate(ctx)

    assert frame.visible is True, "the rail plate stays hidden on the Nanowells page"
    assert refreshed == [True], "the plate is shown without being repainted for this page"
    assert activated == [True], "the page's own activation no longer runs"
