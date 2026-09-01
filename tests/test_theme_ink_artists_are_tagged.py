"""An artist drawn in the plot's ink must be tagged to follow a theme change.

Reported: significance brackets on a saved figure come out "a very light gray,
almost impossible to see". They were drawn in the *screen* theme's ink,
``#E6E9EF``, and a capture flips the card to publication — white paper —
**without redrawing**, by design: ``PlotCard._publication_capture`` suppresses
the recompute so one Save click does not run two. So the bracket went onto white
still near-white, about 1.1:1 against the background.

``setPlotTheme`` restyles the parts of an Axes matplotlib names — spines, ticks,
axis labels, title, legend — and a free ``ax.text`` / ``ax.annotate`` /
``ax.plot`` is not one of those. ``follows_theme`` is the tag that makes the
distinction the library does not, and this guard is what keeps a new
ink-coloured artist from being written without it.

Data colours are deliberately never tagged: repainting one would substitute the
theme's ink for a value the plot is using to mean something.
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import parse_source, python_sources, rel

#: Methods that create a free artist — one matplotlib does not restyle by name.
#: ``set_title`` and the axis-label setters are deliberately absent: those *are*
#: named parts of an Axes and ``setPlotTheme`` already repaints them.
_FREE_ARTIST_METHODS = frozenset({"text", "annotate", "plot"})

#: The function whose result is the plot's ink.
_INK_SOURCE = "tokens_for"

#: The wrapper that marks an artist as ink.
_INK_TAG = "follows_theme"


def _ink_names(func: ast.FunctionDef) -> set[str]:
    """Locals in *func* bound to something ``tokens_for`` returned.

    Both spellings the renderers use: the subscript (``fg = tokens_for(ax)[1]``)
    and the tuple unpack (``_bg, fg, muted, _g, _s = tokens_for(ax)``).
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        source = node.value
        if isinstance(source, ast.Subscript):
            source = source.value
        if not (isinstance(source, ast.Call)
                and isinstance(source.func, ast.Name)
                and source.func.id == _INK_SOURCE):
            continue
        for target in node.targets:
            elements = (target.elts if isinstance(target, (ast.Tuple, ast.List))
                        else [target])
            names.update(e.id for e in elements if isinstance(e, ast.Name))
    return names


def _is_ink(expression, ink_names: set[str]) -> bool:
    """Whether *expression* evaluates to a token from ``tokens_for``."""
    if isinstance(expression, ast.Name):
        return expression.id in ink_names
    if isinstance(expression, ast.Subscript):
        expression = expression.value
    return (isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == _INK_SOURCE)


def _tagged_calls(func: ast.FunctionDef) -> set[int]:
    """``id()`` of every artist-creating call *func* tags, however it tags it.

    Two spellings, and a guard that saw only the first flagged the very bracket
    this rule was written for. Wrapping the call is one:
    ``follows_theme(ax.text(...))``. Tagging the artist through the name it was
    bound to is the other, and it is the one an ``ax.plot`` needs, because the
    call returns a list that has to be unpacked before there is an artist to
    tag.
    """
    tagged = set()
    tagged_names = set()
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == _INK_TAG):
            continue
        for arg in node.args:
            tagged.add(id(arg))
            if isinstance(arg, ast.Name):
                tagged_names.add(arg.id)
    if tagged_names:
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            bound = set()
            for target in node.targets:
                elements = (target.elts
                            if isinstance(target, (ast.Tuple, ast.List))
                            else [target])
                bound.update(e.id for e in elements if isinstance(e, ast.Name))
            if bound & tagged_names:
                tagged.add(id(node.value))
    return tagged


def _untagged_ink_artists(tree) -> list[tuple[int, str]]:
    """Every free artist drawn in the ink without the tag, as ``(line, call)``."""
    offenders = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ink_names = _ink_names(func)
        tagged = _tagged_calls(func)
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _FREE_ARTIST_METHODS):
                continue
            colour = next((kw.value for kw in node.keywords if kw.arg == "color"),
                          None)
            if colour is None or not _is_ink(colour, ink_names):
                continue
            if id(node) in tagged:
                continue
            offenders.append((node.lineno, f".{node.func.attr}(…, color=ink)"))
    return offenders


def test_every_ink_coloured_artist_follows_the_theme():
    offenders = []
    for path in python_sources():
        for line, call in _untagged_ink_artists(parse_source(path)):
            offenders.append(f"{rel(path)}:{line}: {call}")
    assert not offenders, (
        "an artist coloured from `tokens_for` is chrome, and a capture flips "
        "the card to publication without redrawing — so it keeps the screen "
        "theme's near-white ink and lands on white paper. Wrap it in "
        "`core.plot_style.follows_theme(...)` (pass `muted=True` for the "
        "secondary ink):\n  " + "\n".join(offenders))


# ── the repaint itself ───────────────────────────────────────────────────────

@pytest.fixture
def card(qapp):
    import theme
    from widgets.plot_card import PlotCard

    theme.apply_theme("blue")
    card = PlotCard(None, figsize=(4, 3), header=False)
    card.setPlotTheme("screen")
    return card


def test_a_tagged_artist_is_repainted_for_the_capture(card):
    """The reported defect, at the boundary that decides what the file holds."""
    from well_viewer.core.plot_style import follows_theme, tokens_for

    ax = card.figure.add_subplot(111)
    screen_ink = tokens_for(ax)[1]
    (bracket,) = ax.plot([0, 0, 1, 1], [1, 1.1, 1.1, 1], color=screen_ink)
    follows_theme(bracket)
    star = follows_theme(ax.text(0.5, 1.1, "**", color=screen_ink))

    card.setPlotTheme("publication")
    publication_ink = tokens_for(ax)[1]
    assert publication_ink != screen_ink, "the two themes must differ to test this"
    assert bracket.get_color() == publication_ink
    assert star.get_color() == publication_ink


def test_the_muted_role_takes_the_muted_token(card):
    from well_viewer.core.plot_style import follows_theme, tokens_for

    ax = card.figure.add_subplot(111)
    note = follows_theme(ax.text(0, 0, "note", color=tokens_for(ax)[2]),
                         muted=True)
    card.setPlotTheme("publication")
    assert note.get_color() == tokens_for(ax)[2]


def test_an_untagged_data_colour_is_left_alone(card):
    """Repainting a data colour would substitute the theme's ink for a value
    the plot is using to mean something."""
    ax = card.figure.add_subplot(111)
    (series,) = ax.plot([0, 1], [1, 2], color="#4C9AFF")
    card.setPlotTheme("publication")
    assert series.get_color() == "#4C9AFF"


def test_the_screen_ink_comes_back_when_the_capture_ends(card):
    """A capture is a round trip; the card is left as the user had it."""
    from well_viewer.core.plot_style import follows_theme, tokens_for

    ax = card.figure.add_subplot(111)
    screen_ink = tokens_for(ax)[1]
    star = follows_theme(ax.text(0, 0, "**", color=screen_ink))
    with card._publication_capture():
        assert star.get_color() != screen_ink
    assert star.get_color() == screen_ink


def test_a_figure_level_footnote_is_repainted_too(card):
    """A footnote written with ``fig.text`` belongs to no axes, so a repaint
    that walked only ``figure.axes`` left it in the outgoing theme's ink."""
    from well_viewer.core.plot_style import follows_theme, tokens_for

    ax = card.figure.add_subplot(111)
    screen_muted = tokens_for(ax)[2]
    note = follows_theme(card.figure.text(0.5, 0.005, "not shown: x",
                                          color=screen_muted), muted=True)
    card.setPlotTheme("publication")
    assert note.get_color() == tokens_for(ax)[2] != screen_muted


# ── the sibling the rule above cannot see ────────────────────────────────────
#
# ``_untagged_ink_artists`` starts by reading the call's ``color=``, so an
# artist with no ``color=`` at all is invisible to it — and that is the *worse*
# case, not a lesser one. An uncoloured ``ax.text`` takes matplotlib's black,
# which is legible on the publication theme and near-invisible on the screen
# theme's ``#131A24``; and having no ink token in it, it is not tagged either,
# so ``repaint_theme_ink`` walks straight past it and no later flip corrects it.
#
# Response Curves shipped three of these — the omission caption under the axes
# and both sentences of the empty state, which is the drawing whose entire job
# is to be read. The guard above was green throughout, because the rule it
# enforces is about artists that *have* the ink and the defect was artists that
# have no colour at all. §7.6 #3: after fixing a defect, ask what else is
# decidable from the same syntax tree.

#: Receivers whose ``text`` / ``annotate`` draws onto a themed figure. Matching
#: on the receiver name rather than on the module keeps the scan surface the
#: whole tree (§7.5 #3) instead of a hand-picked list of renderers, which is
#: what a rule about "every plot surface" needs.
_PLOT_RECEIVERS = frozenset({"ax", "axes", "fig", "figure"})

#: ``module::function`` sites entitled to matplotlib's default black, with the
#: reason. Exempted by function rather than by file (§7.5 #10): a file-wide
#: exemption here would cover every future artist in the same module.
_ENTITLED_TO_BLACK = {
    # The image-table export draws each cell onto an axes it has just set to
    # white (`ax.set_facecolor("none" if transparent_bg else "white")`, a few
    # lines above), and the placeholder is the text inside one such cell. It is
    # not on a PlotCard and follows no Screen/Publication toggle, so there is no
    # theme for it to follow and black is the correct ink.
    "well_viewer/features/image_browser/image_table/export.py::_build_export_figure",
}


def _uncoloured_free_artists(tree, module: str) -> list[tuple[int, str]]:
    """Every free text artist drawn with no colour at all, as ``(line, call)``."""
    offenders = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if f"{module}::{func.name}" in _ENTITLED_TO_BLACK:
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("text", "annotate")):
                continue
            receiver = node.func.value
            name = (receiver.id if isinstance(receiver, ast.Name)
                    else getattr(receiver, "attr", ""))
            if name not in _PLOT_RECEIVERS:
                continue
            if any(kw.arg in ("color", "c") for kw in node.keywords):
                continue
            offenders.append(
                (node.lineno, f"{func.name}: {name}.{node.func.attr}(…) — no colour"))
    return offenders


def test_no_free_artist_is_left_on_matplotlibs_default_black():
    offenders = []
    for path in python_sources():
        module = rel(path)
        for line, call in _uncoloured_free_artists(parse_source(path), module):
            offenders.append(f"{module}:{line}: {call}")
    assert not offenders, (
        "a free `ax.text` / `ax.annotate` with no `color=` is black on both "
        "themes — unreadable on the screen theme's #131A24 — and carries no "
        "ink tag, so `repaint_theme_ink` never corrects it. Colour it from "
        "`tokens_for(ax)` (index 1 for the primary ink, 2 for muted) and wrap "
        "it in `follows_theme(...)`. A site genuinely drawing onto a non-themed "
        "figure goes in `_ENTITLED_TO_BLACK` with its reason:\n  "
        + "\n  ".join(offenders))


def test_the_entitlement_roster_names_only_live_sites():
    """An exemption that no longer describes a real call is indistinguishable
    from one somebody added to make a failure go away."""
    live = set()
    for path in python_sources():
        module = rel(path)
        tree = parse_source(path)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = f"{module}::{func.name}"
            if key not in _ENTITLED_TO_BLACK:
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("text", "annotate")
                        and not any(kw.arg in ("color", "c")
                                    for kw in node.keywords)):
                    live.add(key)
    stale = sorted(_ENTITLED_TO_BLACK - live)
    assert not stale, (
        f"these entitlements no longer describe an uncoloured artist: {stale}")
