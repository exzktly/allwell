"""Regression: the global Screen/Publication toggle must flip every plot tab.

``AllWellApp._on_present_toggled`` hardcodes which widget-registry
attributes name a "known" PlotCard rather than discovering them -- Spatial
and Multivariate were added to the app without being added to that list, so
the toggle silently did nothing on those two tabs (colors never changed)
even though both tabs' own PlotCard/renderer wiring was otherwise correct.
Exercises the real method via an unbound call over a lightweight stub, since
building a full ``AllWellApp`` is unnecessary for this pure attribute-walk.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


class _FakeCard:
    def __init__(self):
        self.themes: list[str] = []

    def setPlotTheme(self, mode: str) -> None:
        self.themes.append(mode)


class _FakeButton:
    def setToolTip(self, _text: str) -> None:
        pass


def test_toggle_flips_every_registered_card_including_spatial_and_multivariate():
    from allwell import AllWellApp

    cards = {
        name: _FakeCard() for name in (
            "_line_card", "_bar_card", "_scatter_card", "_scatter_agg_card",
            "_distribution_card", "_heatmap_card", "_sp_card", "_mv_card",
        )
    }
    stub = SimpleNamespace(
        _present_mode="screen",
        _present_btn=_FakeButton(),
        _review=SimpleNamespace(**cards),
    )

    AllWellApp._on_present_toggled(stub, True)

    assert stub._present_mode == "publication"
    for name, card in cards.items():
        assert card.themes == ["publication"], f"{name} was never flipped"


def test_toggle_back_to_screen_flips_every_card_too():
    from allwell import AllWellApp

    cards = {
        name: _FakeCard() for name in ("_sp_card", "_mv_card", "_heatmap_card")
    }
    stub = SimpleNamespace(
        _present_mode="publication",
        _present_btn=_FakeButton(),
        _review=SimpleNamespace(**cards),
    )

    AllWellApp._on_present_toggled(stub, False)

    assert stub._present_mode == "screen"
    for name, card in cards.items():
        assert card.themes == ["screen"], f"{name} was never flipped"
