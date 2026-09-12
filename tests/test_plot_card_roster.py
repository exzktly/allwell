"""The plot-card roster has one home (ARCHITECTURE §5.3).

Two hardcoded allowlists used to carry the same eight widget-registry names:
``core/plot_card_actions._CARDS`` (which resolves "the first visible card" for
the global Copy SVG / Save Figure actions) and a tuple inside
``allwell.py::_on_present_toggled`` (the Screen/Publication toggle). §5.3
records what a divergence costs — a new plot tab missing from one of them
silently gets no Screen/Publication toggle or no global Copy/Save, which is the
bug that shipped for Spatial and Multivariate.

Neither list can be derived from the feature registry today, so the roster stays
hand-written. What this test enforces is that it is hand-written *once*: the
shell reads ``PLOT_CARD_ATTRS`` instead of restating the names.

**Written once is not the same as written completely**, and this file only
covered the first half. Response Curves registered a ``PlotCard`` and was never
added to the roster, so the Screen/Publication toggle — which iterates
``PLOT_CARD_ATTRS`` and calls ``setPlotTheme`` on each — skipped it entirely.
That is exactly the Spatial and Multivariate bug this file was written for,
shipped a third time while the file that names it stayed green: no copy had
diverged, because the one copy was simply short. The completeness test below is
the missing half, and it derives the expected set from the source rather than
restating it — a roster of rosters is the same defect one level up.
"""

from __future__ import annotations

import ast
from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source


def test_the_shell_imports_the_roster_rather_than_restating_it():
    from review.core.plot_card_actions import PLOT_CARD_ATTRS

    offenders = []
    # Not just `allwell.py`: `core/centre_view.py` carried two more copies of
    # the roster (Configure-subplots and Edit-axes), both six-of-eight, both
    # still broken on Spatial and Multivariate after the first pass fixed the
    # Ctrl+E one. A guard that reads a single file cannot see the next copy.
    for path in python_sources():
        if path.name.startswith("plot_card_actions"):
            continue
        tree = parse_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            names = [e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            # Count the names that look like a widget-registry card attr —
            # leading underscore and a `_card` tail. Two or more of those is a
            # roster even when other strings sit beside them: the first version
            # required `all()`, which a single non-card entry defeated. The
            # leading underscore is what keeps an `__all__` list holding
            # `btn_card` from reading as one.
            cards = [n for n in names if n.startswith("_") and n.endswith("_card")]
            if len(cards) >= 2:
                offenders.append(f"{rel(path)}:{node.lineno} — {cards}")
    assert not offenders, (
        "A module writes out its own list of plot-card names. Import "
        "`plot_card_actions.PLOT_CARD_ATTRS` instead, so the roster cannot "
        "diverge from the one the Copy/Save actions read (ARCHITECTURE "
        f"§5.3). Current roster: {list(PLOT_CARD_ATTRS)}\n  "
        + "\n  ".join(offenders)
    )


def test_every_roster_entry_has_a_save_as_filename():
    """``PLOT_CARD_ATTRS`` is derived from ``_CARDS`` rather than maintained
    beside it, so the two cannot fall out of step."""
    from review.core.plot_card_actions import _CARDS, PLOT_CARD_ATTRS

    assert PLOT_CARD_ATTRS == tuple(attr for attr, _ in _CARDS)
    assert all(default.endswith(".png") for _, default in _CARDS)


def test_the_roster_holds_every_plot_card_the_app_builds():
    """Every ``PlotCard`` a feature registers is in the roster.

    The toggle and the global Copy/Save actions act on the roster and nothing
    else, so a card missing from it is a card those actions cannot see — and the
    failure is silent, because the other eight still work. Response Curves shows
    the shape: its plate followed no Screen/Publication toggle in either
    direction, and a redraw did not correct it, because the renderer asks the
    card what theme it is in and the card had never been told.

    The expected set is derived by scanning for the registration itself
    (``register("_x_card", ...)`` in a feature that constructs a ``PlotCard``),
    so adding a tab and forgetting the roster fails here rather than shipping.
    """
    import re

    from review.core.plot_card_actions import PLOT_CARD_ATTRS

    registered = {}
    for path in python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "PlotCard(" not in text:
            continue
        for name in re.findall(r'register\(\s*"(_[a-z0-9_]*_card)"', text):
            registered[name] = rel(path)

    # Home's two cards are not plot tabs: they host a gallery thumbnail and a
    # recent-datasets tile, neither of which has axes to restyle or a figure to
    # copy. They are named here so that "not in the roster" is a decision on
    # record rather than another omission that looks like one.
    not_plot_tabs = {"_home_recents_card", "_home_saved_plots_card"}

    missing = sorted(set(registered) - set(PLOT_CARD_ATTRS) - not_plot_tabs)
    assert not missing, (
        "These features register a PlotCard that the roster does not list, so "
        "the Screen/Publication toggle and the global Copy SVG / Save Figure "
        "actions cannot reach them:\n  "
        + "\n  ".join(f"{name} ({registered[name]})" for name in missing)
        + f"\nAdd them to `plot_card_actions._CARDS`. Current roster: "
        f"{list(PLOT_CARD_ATTRS)}")


def test_the_roster_lists_no_card_nothing_registers():
    """The converse, and a separate test on purpose.

    A roster entry for a card no feature registers is a name that never
    resolves, which reads as "handled" at every call site. It is asserted apart
    from the completeness half because the two answer to different scans: this
    one is only meaningful over the *whole* tree, so ``test_guards_bite.py``
    — which proves a guard by pointing its scan at one synthetic file — can
    exercise the half above and not this one.
    """
    import re

    from review.core.plot_card_actions import PLOT_CARD_ATTRS

    registered = set()
    for path in python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "PlotCard(" not in text:
            continue
        registered.update(re.findall(r'register\(\s*"(_[a-z0-9_]*_card)"', text))

    stale = sorted(set(PLOT_CARD_ATTRS) - registered)
    assert not stale, (
        f"The roster lists cards nothing registers: {stale}")
