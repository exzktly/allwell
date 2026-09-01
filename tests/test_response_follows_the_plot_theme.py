"""Response Curves follows the Screen/Publication toggle.

Reported: the response plot did not respect the dark/white background toggle.
Not in either direction, and a redraw did not correct it.

The toggle is the title bar's Presentation button (``allwell.py::
_on_present_toggled``). It does not walk the widget tree — it iterates
``plot_card_actions.PLOT_CARD_ATTRS`` and calls ``setPlotTheme`` on each name.
``_response_card`` was not in that roster, so the card was never told; the
renderer then asks the card what theme it is in (``plot_style.tokens_for`` reads
``fig._plot_card.plotTheme()``) and faithfully repainted the stale answer, which
is why redrawing did not help either.

``tests/test_plot_card_roster.py`` guards the roster's contents. This file
guards the consequence, because a name in a tuple is not a repainted figure —
§7.4 #1, assert the outbound effect rather than the value behind it. It drives
the shell's loop verbatim rather than calling ``card.setPlotTheme`` directly:
calling it directly is what every earlier probe did, and it is precisely the
call the bug consisted of skipping.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")

_PLOT_LEAVES = ("Bar Plots", "Line Graphs", "Response Curves", "Scatter Plot",
                "Distribution", "Heat Map")


def _build_every_plot_tab(app):
    """Visit each plot leaf so its card exists — they build lazily."""
    from PySide6.QtWidgets import QApplication
    app.show()
    app._notebook.setCurrentByName("Plotting")
    for leaf in _PLOT_LEAVES:
        app._plotting_notebook.setCurrentByName(leaf)
        for _ in range(6):
            QApplication.processEvents()


def _flip_the_way_the_shell_does(ctx, mode):
    """``allwell.py::_on_present_toggled``'s loop, verbatim."""
    from PySide6.QtWidgets import QApplication
    from well_viewer.core.plot_card_actions import PLOT_CARD_ATTRS
    for attr in PLOT_CARD_ATTRS:
        card = ctx.widgets.get_or_none(attr)
        if card is not None and hasattr(card, "setPlotTheme"):
            card.setPlotTheme(mode)
    for _ in range(6):
        QApplication.processEvents()


@pytest.mark.slow
def test_the_toggle_reaches_the_response_card(loaded_app):
    """The reported bug, in the direction it was reported and back again."""
    app = loaded_app
    ctx = app._ctx
    _build_every_plot_tab(app)
    card = ctx.widgets.get("_response_card")

    _flip_the_way_the_shell_does(ctx, "publication")
    assert card.plotTheme() == "publication", (
        "the Presentation toggle left the Response card on "
        f"{card.plotTheme()!r} — it is not in PLOT_CARD_ATTRS, so the shell's "
        "loop never reached it")

    _flip_the_way_the_shell_does(ctx, "screen")
    assert card.plotTheme() == "screen", (
        "the toggle reaches Response going one way but not back")


@pytest.mark.slow
def test_the_toggle_reaches_every_plot_card(loaded_app):
    """Not only Response. The roster has now been short three times — Spatial,
    Multivariate, and this — so the assertion is over all of them."""
    from well_viewer.core.plot_card_actions import PLOT_CARD_ATTRS

    app = loaded_app
    ctx = app._ctx
    _build_every_plot_tab(app)
    for top in ("Spatial", "Multivariate"):
        app._notebook.setCurrentByName(top)
    _build_every_plot_tab(app)

    _flip_the_way_the_shell_does(ctx, "publication")
    built = {a: ctx.widgets.get_or_none(a) for a in PLOT_CARD_ATTRS}
    stuck = [a for a, c in built.items()
             if c is not None and c.plotTheme() != "publication"]
    assert not stuck, f"the toggle did not reach {stuck}"


@pytest.mark.slow
def test_the_response_figure_actually_repaints(loaded_app):
    """The card's mode is a means; the figure's ink is the end.

    Asserts the axes background is the theme's, in both directions — a card
    that recorded the mode and drew nothing would satisfy the test above.
    """
    from matplotlib.colors import to_rgba
    from widgets.plot_card import plot_tokens

    app = loaded_app
    ctx = app._ctx
    _build_every_plot_tab(app)
    card = ctx.widgets.get("_response_card")

    seen = {}
    for mode in ("publication", "screen"):
        _flip_the_way_the_shell_does(ctx, mode)
        want = tuple(round(v, 3) for v in to_rgba(plot_tokens(mode)[0]))
        for ax in card.figure.axes:
            got = tuple(round(v, 3) for v in ax.get_facecolor())
            assert got == want, (
                f"in {mode} mode the response axes are {got}, not the theme's "
                f"{want}")
        seen[mode] = want
    assert seen["publication"] != seen["screen"], (
        "the two themes share a background colour, so this test cannot tell "
        "them apart")


# ── the renderer's own ink ──────────────────────────────────────────────────
#
# The roster was the reported bug and the tests above are its guard. These
# cover the second half of the same defect, which the roster fix does not
# touch: three of the renderer's ``ax.text`` calls set no colour and carried no
# ``follows_theme`` tag, so they took matplotlib's black whatever the card was
# set to. On the screen theme that is near-invisible against ``#131A24``, and
# because ``repaint_theme_ink`` walks only the tagged artists, a later flip
# left them black too.
#
# Fast lane: a real ``PlotCard`` is a widget, not an app, and it is what
# ``tokens_for`` reads. Stubbing an object with a ``plotTheme`` method would be
# §7.8's first face — a fixture that cannot disagree with the code under test.

def _themed_axes(mode: str):
    """A real card set to *mode*, and an axes on its figure."""
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(4, 3))
    card.setPlotTheme(mode)
    return card, card.figure.add_subplot(1, 1, 1)


def _ink(artist):
    """``(upper-case hex colour, ink tag)`` for one text artist."""
    from matplotlib.colors import to_hex
    from widgets.plot_card import THEME_INK_ATTR

    return to_hex(artist.get_color()).upper(), getattr(artist, THEME_INK_ATTR, None)


def _a_curve_with_an_omission():
    from well_viewer.features.response import collect as rc

    points = (rc.ResponsePoint("m0", 1.0, 5.0, 0.5, 3, "1"),
              rc.ResponsePoint("m1", 10.0, 9.0, 0.5, 3, "10"))
    return rc.ResponseData(variable_name="Dose", unit="uM",
                           curves=[rc.ResponseCurve(24.0, points)],
                           omitted=[("B2", rc.NO_VALUE)])


def _nothing_to_draw():
    from well_viewer.features.response import collect as rc

    return rc.ResponseData(variable_name="Dose", curves=[],
                           omitted=[("B2", rc.NO_VALUE)])


@pytest.mark.qt
@pytest.mark.parametrize("mode", ["screen", "publication"])
def test_the_caption_under_the_axes_is_drawn_in_the_theme_ink(qapp, mode):
    """The omission caption, in both themes.

    Asserts the exact token rather than "it is not black": a property is
    satisfied by a set of implementations and only one of them is right
    (§7.8 #1). Neither theme's muted ink is black, so the pre-fix bare
    ``ax.text`` fails this in both directions.
    """
    from well_viewer.features.response.renderer import draw_response
    from widgets.plot_card import plot_tokens

    _card, ax = _themed_axes(mode)
    draw_response(None, ax, _a_curve_with_an_omission(),
                  title="Response vs Dose", ylabel="GFP mean")

    captions = [t for t in ax.texts if "omitted" in t.get_text()]
    assert len(captions) == 1, (
        f"expected one omission caption on the axes, found {len(captions)}")
    assert _ink(captions[0]) == (plot_tokens(mode)[2].upper(), "muted")


@pytest.mark.qt
@pytest.mark.parametrize("mode", ["screen", "publication"])
def test_the_empty_state_sentences_are_drawn_in_the_theme_ink(qapp, mode):
    """The reason and its caption — the one drawing whose whole job is to be
    read, and the one a user meets before they have picked a variable."""
    from well_viewer.features.response.renderer import draw_response
    from widgets.plot_card import plot_tokens

    _card, ax = _themed_axes(mode)
    draw_response(None, ax, _nothing_to_draw(), title="Response vs Dose")

    _bg, fg, muted, _grid, _spine = (t.upper() for t in plot_tokens(mode))
    by_text = {t.get_text(): _ink(t) for t in ax.texts if t.get_text()}
    reason = next(t for t in by_text if "Dose value" in t)
    caption = next(t for t in by_text if "omitted" in t)
    assert by_text[reason] == (fg, "fg")
    assert by_text[caption] == (muted, "muted")
