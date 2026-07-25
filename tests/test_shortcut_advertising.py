"""The chrome must never advertise a shortcut that isn't wired — or hide one.

``AllWellApp._install_shortcuts`` says it plainly: "Every shortcut here is
surfaced somewhere in the chrome (statusbar key-hints or the help drawer's
Keyboard-shortcuts list); keep the two in sync so the UI never advertises a
binding that isn't wired."

That rule was review-only, and it drifted: ``Ctrl+B`` was described as the
sidebar-collapse shortcut in the docs and in two code comments while no
``QShortcut`` for it existed, and ``Ctrl+L`` was wired but missing from the help
drawer. This test pins both directions:

* every installed ``QShortcut`` appears in the help drawer's list, and
* every status-bar key hint corresponds to a real binding (the status bar shows
  a deliberate *subset* — the most-used three — so it need not be exhaustive).

Pure text parse of ``allwell.py``: no Qt, no app construction, fast lane.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLWELL = REPO_ROOT / "allwell.py"

# The help drawer renders arrow keys as glyphs; QKeySequence spells them out.
_GLYPH = {"Left": "←", "Right": "→"}


def _source() -> str:
    return ALLWELL.read_text(encoding="utf-8")


def _installed_shortcuts() -> set[str]:
    """Key names passed to ``QKeySequence(...)`` inside ``_install_shortcuts``."""
    tree = ast.parse(_source())
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_install_shortcuts"),
        None,
    )
    assert fn is not None, "allwell.py no longer defines _install_shortcuts"
    keys = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "QKeySequence"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    assert keys, "no QKeySequence(...) bindings found in _install_shortcuts"
    return keys


def _advertised_in_help_drawer() -> set[str]:
    """`Ctrl+X` spellings inside the help drawer's Keyboard-shortcuts block."""
    src = _source()
    start = src.index("<b>Keyboard shortcuts</b>")
    end = src.index("Close window", start)
    return set(re.findall(r"<tt>Ctrl\+(.{1,5}?)</tt>", src[start:end + 40]))


def _status_bar_hints() -> set[str]:
    """The ``⌘X`` glyphs shown as status-bar key-hint chips."""
    src = _source()
    block = src[src.index("# Kbd hints"):src.index("# Application-log tray")]
    return set(re.findall(r'"⌘(.{1,3}?)"', block))


def test_every_installed_shortcut_is_advertised():
    advertised = _advertised_in_help_drawer()
    missing = []
    for key in sorted(_installed_shortcuts()):
        suffix = key.split("+", 1)[1]
        if _GLYPH.get(suffix, suffix) not in advertised:
            missing.append(key)
    assert not missing, (
        "these shortcuts are wired in _install_shortcuts but absent from the "
        f"help drawer's Keyboard-shortcuts list: {missing}. A binding the UI "
        "never mentions is a binding nobody finds."
    )


def test_help_drawer_advertises_nothing_phantom():
    """The reverse direction: the drawer must not list an unwired binding.

    This is the shape the ``Ctrl+B`` drift took — described everywhere,
    implemented nowhere.
    """
    installed = {k.split("+", 1)[1] for k in _installed_shortcuts()}
    installed |= {_GLYPH[s] for s in installed if s in _GLYPH}
    phantom = sorted(a for a in _advertised_in_help_drawer() if a not in installed)
    assert not phantom, (
        f"the help drawer advertises Ctrl+{phantom}, which _install_shortcuts "
        "does not wire. Wire it or drop the line — a shortcut the UI promises "
        "and the app ignores is worse than no shortcut."
    )


def test_no_status_bar_hint_without_a_binding():
    installed = {k.split("+", 1)[1] for k in _installed_shortcuts()}
    installed |= {_GLYPH[s] for s in installed if s in _GLYPH}
    phantom = sorted(h for h in _status_bar_hints() if h not in installed)
    assert not phantom, (
        f"the status bar advertises {phantom}, which _install_shortcuts does "
        "not wire. (This is how 'Search ⌘K' lingered after the global search "
        "was dropped.)"
    )


def test_sidebar_toggle_is_bound():
    """Ctrl+B collapses the sidebar — the binding the docs long claimed existed."""
    assert "Ctrl+B" in _installed_shortcuts(), (
        "Ctrl+B is not wired. The sidebar-collapse shortcut is documented in "
        "README_WellViewer.md, ARCHITECTURE.md §5.4, and the help drawer; it "
        "must actually exist."
    )
