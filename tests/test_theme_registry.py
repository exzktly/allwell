"""The theme registry: what the app starts in, and what it can get back to.

Two things live here that nothing else was checking. The first is the shipped
default — the app starts in ``grey``, and the only thing that says so is one
constant no test read. The second is the shape of the ``THEMES`` literal, which
has a way of going wrong silently: it was written ``{DEFAULT_THEME: {}, "grey":
_GREY}`` while ``DEFAULT_THEME`` was ``"blue"``, so pointing that constant at
``grey`` writes the key ``grey`` twice in one dict literal. Python takes the
second, the registry loses ``blue`` entirely, ``apply_theme("blue")`` starts
raising ``KeyError``, and the palette the token classes hold — the baseline
every other theme is restored *from* — has no name to be reached by. Nothing
about that is a syntax error and nothing about it is visible in a diff.
"""

from __future__ import annotations

import ast

import pytest

from ui import theme as _theme
from tests._repo_scan import REPO_ROOT, parse_source


@pytest.fixture(autouse=True)
def _restore_the_baseline():
    """Leave the token classes holding their own values.

    ``apply_theme`` mutates class attributes process-wide, and the rest of the
    lane runs without applying any theme — so it reads whatever ``Colors`` was
    left holding. ``"blue"`` is not a choice of theme here: it is the name of
    the class defaults, which is the state every other test in the lane assumes.
    """
    yield
    _theme.apply_theme("blue")


def test_the_app_starts_grey():
    """With nothing in the environment naming a theme.

    This is the behaviour the default exists for: point ``DEFAULT_THEME`` back
    at ``blue`` and this is what goes red.
    """
    assert _theme.DEFAULT_THEME == "grey"


def test_an_unset_environment_variable_applies_the_default(monkeypatch):
    """``allwell.main`` calls this and nothing else, so it is the whole path
    between the constant and what a user sees."""
    monkeypatch.delenv("ALLWELL_THEME", raising=False)
    assert _theme.select_theme_from_env() == "grey"
    assert _theme.Colors.surface == _theme.THEMES["grey"]["Colors"]["surface"]


def test_an_unknown_theme_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("ALLWELL_THEME", "chartreuse")
    assert _theme.select_theme_from_env() == _theme.DEFAULT_THEME


def test_blue_is_still_reachable(monkeypatch):
    """It is the baseline, and it is also still a theme a user can ask for."""
    monkeypatch.setenv("ALLWELL_THEME", "blue")
    assert _theme.select_theme_from_env() == "blue"


def test_the_default_names_a_registered_theme():
    assert _theme.DEFAULT_THEME in _theme.THEMES


def test_the_baseline_palette_has_a_name_to_be_restored_by():
    """``apply_theme`` resets every themeable attribute to the value captured
    from the class at import, then applies the named bundle. ``blue`` overrides
    nothing, so applying it *is* the reset — and a registry without it has no
    way back to the palette the classes hold."""
    assert _theme.THEMES["blue"] == {}

    baseline_surface = _theme.Colors.surface
    _theme.apply_theme("grey")
    grey_surface = _theme.Colors.surface
    assert grey_surface != baseline_surface
    _theme.apply_theme("blue")
    assert _theme.Colors.surface == baseline_surface


@pytest.mark.parametrize("name", _theme.available_themes())
def test_every_registered_theme_applies(name):
    assert _theme.apply_theme(name) == name


def _themes_assignment() -> ast.Dict:
    tree = parse_source(REPO_ROOT / "ui" / "theme.py")
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "THEMES"
                        for t in node.targets)):
            assert isinstance(node.value, ast.Dict), (
                "THEMES is no longer a dict literal; this guard reads the "
                "literal's keys and can no longer see them")
            return node.value
    raise AssertionError("no module-level THEMES assignment in ui/theme.py")


def test_every_theme_name_is_spelled_out_in_the_registry():
    """A key that is a *reference* can silently collide with a key beside it.

    ``{DEFAULT_THEME: {}, "grey": _GREY}`` reads as two entries and is two
    entries only while ``DEFAULT_THEME`` is not ``"grey"``. Repointing the
    constant makes it one entry, the second wins, and the registry quietly
    loses a theme — including, in the case that nearly shipped, the baseline
    palette everything else is restored from.
    """
    offenders = [
        ast.unparse(key)
        for key in _themes_assignment().keys
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str))
    ]
    assert not offenders, (
        "these THEMES keys are references rather than string literals, so two "
        "of them can resolve to the same name and Python will keep only the "
        f"last silently: {offenders}")


def test_the_registry_keys_are_distinct():
    """The half the literal check cannot see: a duplicate *literal* has already
    collapsed by the time the dict exists, so count the keys in the source."""
    keys = [key.value for key in _themes_assignment().keys
            if isinstance(key, ast.Constant)]
    assert len(keys) == len(set(keys)), f"duplicate theme names in THEMES: {keys}"
    assert set(keys) == set(_theme.THEMES), (
        "a THEMES key in the source is not in the registry at runtime, which is "
        "what a collision looks like from the outside")
