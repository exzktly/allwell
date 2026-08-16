"""Every registered figure has a stable preference key.

``plot_style.figure_pref_key`` maps a figure to the string its axis and mark
preferences are stored under, and falls back to ``f"fig:{id(fig)}"`` for one it
does not recognise. That fallback is a memory address: stable within a session,
different in the next, so anything stored under it is written to
``persistence.json`` and can never match again. The user styles a figure, quits,
reopens, and the styling is gone with nothing said — while the dead entry stays
in the file for ever, since nothing prunes it and ``figure_prefs`` creates one
on *read*.

Three figures were reaching that fallback — Spatial, Multivariate and
Statistics — and the export editor opens two of them by name
(``features/export/style.py``), so it was the ordinary path for those tabs
rather than an edge case. The map had six entries and the app had nine figures;
nothing connected the two, so the three that arrived later simply were not
added.

This is that connection: every ``register("_*_fig", …)`` call in the tree must
have a ``_FIG_PREF_KEY_MAP`` entry.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel


def _registered_figure_names(source: str) -> set[str]:
    """Registry names ending in ``_fig`` that *source* registers.

    Read from the ``register(...)`` call rather than from any string that looks
    like a figure name: ``_is_line_fig`` is a predicate on the export sidebar,
    not a figure, and appears as a bare literal elsewhere.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the tree parses
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.endswith("_fig"):
                out.add(first.value)
    return out


def registered_figures() -> dict[str, str]:
    """``{registry name: the file that registers it}``."""
    found: dict[str, str] = {}
    for path in python_sources():
        for name in _registered_figure_names(path.read_text(encoding="utf-8")):
            found.setdefault(name, rel(path))
    return found


def test_every_registered_figure_has_a_stable_pref_key():
    from well_viewer.core.plot_style import _FIG_PREF_KEY_MAP

    mapped = {attr for attr, _key in _FIG_PREF_KEY_MAP}
    missing = sorted(
        (name, where) for name, where in registered_figures().items()
        if name not in mapped
    )
    assert not missing, (
        "These figures are registered but have no _FIG_PREF_KEY_MAP entry, so "
        "figure_pref_key falls back to id(fig) and their axis / mark "
        "preferences cannot survive a reload:\n  "
        + "\n  ".join(f"{name}  (registered in {where})" for name, where in missing)
    )


def test_the_key_map_has_no_stale_entries():
    """An entry naming a figure nothing registers is not describing the app."""
    from well_viewer.core.plot_style import _FIG_PREF_KEY_MAP

    registered = set(registered_figures())
    stale = sorted(attr for attr, _key in _FIG_PREF_KEY_MAP if attr not in registered)
    assert not stale, (
        "These _FIG_PREF_KEY_MAP entries name a figure no module registers — "
        "delete them:\n  " + "\n  ".join(stale)
    )


def test_the_keys_are_distinct():
    """Two figures sharing a key would silently share one set of prefs."""
    from well_viewer.core.plot_style import _FIG_PREF_KEY_MAP

    keys = [key for _attr, key in _FIG_PREF_KEY_MAP]
    assert len(keys) == len(set(keys)), f"duplicate pref key(s) in {keys}"
