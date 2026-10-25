"""Only the settings file writes the app-wide Properties style; every plot writes its own.

A Properties edit must restyle the plot it was made on and no other. The per-figure store
(``ExportPrefsState.axis_prefs``, reached through ``plot_style.figure_prefs``) is where every edit goes; the
app-wide dict (``ExportPrefsState.style_prefs``) is only what an untouched plot reads for a key it has never been
given — the style saved before preferences became per plot. Anything else that writes it restyles every plot at once,
which is how a Batch Export Style Preset came to put Helvetica 22 on every plot in the app.

So the dict is reachable only from the modules that own it: ``plot_style`` (which seeds and reads it), the persistence
layer (which restores it from disk) and the state class it lives on. A feature that needs a plot's style asks
``plot_style.figure_prefs`` (a figure on screen) or ``plot_style.plot_style_of`` (a figure drawn elsewhere, by key).
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: Each module entitled to touch the app-wide dict, and why.
OWNERS = {
    "review/core/plot_style.py": "seeds the dict with the defaults and reads it as the fallback for an unset key",
    "review/persistence/view_state.py": "restores the dict from a settings file and saves its non-default keys",
    "review/core/export_prefs_state.py": "declares the field",
}

_NAMES = {"style_prefs", "_ensure_export_style_prefs"}


def _reaches(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _NAMES:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in _NAMES:
            lines.append(node.lineno)
        elif isinstance(node, ast.alias) and node.name in _NAMES:
            lines.append(getattr(node, "lineno", 0))
    return sorted(set(lines))


def test_nothing_but_its_owners_reaches_the_app_wide_style():
    found = []
    for path in python_sources():
        if rel(path) in OWNERS:
            continue
        found += [f"{rel(path)}:{line}" for line in _reaches(parse_source(path))]
    assert not found, (
        "these reach the app-wide Properties style, which restyles every plot at once; read a plot's own style "
        "through plot_style.figure_prefs / plot_style_of instead:\n  " + "\n  ".join(found))


def test_every_owner_still_reaches_it():
    """An owner that no longer touches the dict is an exemption describing nothing; drop it from the roster."""
    stale = [name for name in OWNERS if not _reaches(parse_source(rel_path(name)))]
    assert not stale, f"no longer reach the app-wide style, remove from OWNERS: {stale}"


def rel_path(name: str):
    from tests._repo_scan import REPO_ROOT

    return REPO_ROOT / name
