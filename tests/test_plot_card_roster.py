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
"""

from __future__ import annotations

import ast
from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source


def test_the_shell_imports_the_roster_rather_than_restating_it():
    from well_viewer.core.plot_card_actions import PLOT_CARD_ATTRS

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
    from well_viewer.core.plot_card_actions import _CARDS, PLOT_CARD_ATTRS

    assert PLOT_CARD_ATTRS == tuple(attr for attr, _ in _CARDS)
    assert all(default.endswith(".png") for _, default in _CARDS)
