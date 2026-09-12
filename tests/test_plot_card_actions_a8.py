"""A8 P3.3 — the active-card Copy/Save actions as ctx-based functions.

Proves the extraction of ``_copy_active_card_as_svg`` / ``_save_active_card_figure``
off the god-object into ``core.plot_card_actions``: the active card is resolved
through the widget registry (``ctx.widgets``), and the two app/window-level
services (status bar, Save-As dialog) are the only remaining god-object touches.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core import plot_card_actions as pca
from review.viewer_context import ViewerContext


class _FakeCard:
    def __init__(self, fig, visible=True):
        self.figure = fig
        self._plot_theme = "screen"
        self._visible = visible
        self.themes = []

    def isVisible(self):
        return self._visible

    def setPlotTheme(self, t):
        self._plot_theme = t
        self.themes.append(t)


class _FakeRegistry:
    def __init__(self, mapping):
        self._m = mapping

    def get_or_none(self, name):
        return self._m.get(name)


def _ctx(cards, app, figures=None):
    # A real ViewerContext so the named service accessors (notify) delegate to the
    # fake app; ``figures`` can be injected to observe the FigureService seam (A5).
    # The real feature registry too: copying a figure reaches the export-style
    # library through the feature-service lookup, so a ctx without one would make
    # the copy return its "not available in this build" default instead.
    from review.features.registry import build_feature_registry
    return ViewerContext(_window=app, state=None, bus=None,
                         widgets=_FakeRegistry(cards), figures=figures,
                         features=build_feature_registry())


def test_active_card_picks_first_visible_in_priority_order():
    hidden = _FakeCard("figH", visible=False)
    visible = _FakeCard("figV", visible=True)
    ctx = _ctx({"_line_card": hidden, "_bar_card": visible}, SimpleNamespace())
    card, default = pca._active_card(ctx)
    assert card is visible
    assert default == "bars.png"


def test_active_card_none_when_all_hidden():
    ctx = _ctx({"_heatmap_card": _FakeCard("f", visible=False)}, SimpleNamespace())
    assert pca._active_card(ctx) == (None, None)


def test_save_routes_figure_to_app_save_dialog():
    calls = []
    figures = SimpleNamespace(save=lambda fig, name: calls.append((fig, name)))
    ctx = _ctx({"_distribution_card": _FakeCard("theFig")}, SimpleNamespace(), figures=figures)
    pca.save_active_card_figure(ctx)
    assert calls == [("theFig", "distribution.png")]


def test_save_noop_when_no_visible_card():
    calls = []
    app = SimpleNamespace(_save_matplotlib_fig=lambda *a: calls.append(a))
    ctx = _ctx({"_bar_card": _FakeCard("f", visible=False)}, app)
    pca.save_active_card_figure(ctx)
    assert calls == []


def test_copy_sets_publication_theme_and_pushes_status(monkeypatch):
    # Stub the clipboard helper so the test stays Qt-free.
    import review.features.export.style as style
    monkeypatch.setattr(style, "copy_figure_to_clipboard", lambda fig: "Copied ✓")
    statuses = []
    app = SimpleNamespace(_set_status=lambda s: statuses.append(s))
    card = _FakeCard("theFig")
    ctx = _ctx({"_scatter_card": card}, app)
    pca.copy_active_card_as_svg(ctx)
    assert statuses == ["Copied ✓"]
    # Captured in publication mode, then restored to the prior theme.
    assert "publication" in card.themes
    assert card._plot_theme == "screen"
