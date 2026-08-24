"""`accent2` moves with `accent`, in every theme.

`accent2` is the *neighbouring* hue — the second series colour, the other half
of a progress dial, the companion swatch — and what makes the pair read as one
family is the relationship between them, not either absolute value. The `grey`
theme overrode `accent`, `accent_hover`, `accent_pressed`, `accent_dim` and
`accent_fg` and left `accent2` at the blue theme's value: the hue gap went from
38° to 55° and the saturations diverged (206 against 110), so on every
remote-run plate the two stopped looking related.

A theme that overrides one without the other is the whole failure, so that is
what is checked — not a hand-picked colour distance, which would need a
threshold nobody can defend.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Overriding any of these means the theme has moved its accent family, so it
#: owes an `accent2` that moved with it.
_ACCENT_FAMILY = {"accent", "accent_hover", "accent_pressed", "accent_dim"}


def _theme_overrides() -> dict[str, set[str]]:
    """Every `_<NAME> = {"Colors": {...}}` override table in `theme.py`."""
    tree = ast.parse((REPO_ROOT / "theme.py").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if getattr(key, "value", None) != "Colors" or not isinstance(value, ast.Dict):
                continue
            out[target.id] = {
                k.value for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return out


def test_there_is_at_least_one_override_table():
    """The anchor: if the parse finds nothing, the test below passes on an
    empty dict and measures nothing."""
    tables = _theme_overrides()
    assert tables, "no theme override tables found in theme.py — has the shape changed?"


def test_a_theme_that_moves_its_accent_moves_accent2_too():
    offenders = []
    for name, keys in sorted(_theme_overrides().items()):
        if (keys & _ACCENT_FAMILY) and "accent2" not in keys:
            offenders.append(
                f"{name} overrides {sorted(keys & _ACCENT_FAMILY)} but not accent2")
    assert not offenders, (
        "accent2 is the neighbouring hue to accent; a theme that moves one and "
        "not the other breaks the pairing everywhere the two are drawn "
        "together:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("theme_name", ["blue", "grey"])
def test_the_pair_stays_a_neighbouring_hue(theme_name, qapp):
    """The rendered check behind the structural one. 38° apart in the default
    theme; a theme whose pair drifts far outside that stops reading as one
    family, whatever the override table says."""
    from PySide6.QtGui import QColor

    import theme as _theme

    _theme.apply_theme(theme_name)
    accent = QColor(_theme.Colors.accent)
    accent2 = QColor(_theme.Colors.accent2)
    gap = abs(accent.hue() - accent2.hue())
    assert 20 <= gap <= 60, (
        f"{theme_name}: accent {accent.name()} and accent2 {accent2.name()} are "
        f"{gap}° apart — neighbouring hues sit near 38°")


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    """`apply_theme` is global state; put it back so this file cannot colour
    whatever runs after it."""
    import theme as _theme

    yield
    _theme.apply_theme("blue")
