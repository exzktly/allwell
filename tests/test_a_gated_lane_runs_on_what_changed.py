"""A gated lane runs on what changed in a module, not on the module's being imported.

``scripts/lane_needed.py`` used to start a gated lane whenever a changed file was
imported directly by any test in the lane. ``ui/theme.py`` is imported by nearly
every test, so editing four colour tokens started the nanowell and remote lanes
— about ten minutes locally and two more CI jobs — for a change no test in
either lane could see. And ``tests/_lanes.py``, which holds every roster, counted
as a harness change for both gates whenever any test was added to any list; it
did on 19 of the last 60 merges.

The import half now asks which definitions changed (and which definitions in the
same module use them), and whether a lane test that imports the module refers
to any of them. ``tests/_lanes.py`` reaches a gate only when that gate's own
rosters or triggers differ. Anything undecidable still runs the lane.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lane():
    spec = importlib.util.spec_from_file_location("lane_needed_under_test", ROOT / "scripts" / "lane_needed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BEFORE = '''"""A module."""
import os

LIMIT = 3


class Colors:
    """Tokens."""
    run_done = "#4ADE80"
    run_pending = "#5F6B7C"
    accent = "#3B6FE8"


def over(n):
    """Whether n is over the limit."""
    return n > LIMIT


def unrelated():
    return os.sep
'''


def test_a_changed_class_member_names_only_that_member(lane):
    after = BEFORE.replace('run_done = "#4ADE80"', 'run_done = "#10B981"')
    assert lane.affected_names(BEFORE, after) == {"run_done"}


def test_a_definition_that_uses_a_changed_one_is_affected_too(lane):
    """``over`` reads ``LIMIT``; a test calling ``over`` sees the new limit, so ``over`` is in the set. Without the
    widening, a test of ``over`` would be skipped for a change that alters its answer."""
    after = BEFORE.replace("LIMIT = 3", "LIMIT = 4")
    assert lane.affected_names(BEFORE, after) == {"LIMIT", "over"}


def test_docstrings_and_comments_change_nothing(lane):
    after = BEFORE.replace('"""Tokens."""', '"""The tokens."""').replace(
        '    return os.sep', '    # the separator\n    return os.sep')
    assert lane.affected_names(BEFORE, after) == set()


@pytest.mark.parametrize("after", [
    BEFORE.replace("import os", "import os\nimport sys"),            # a module statement that defines nothing
    BEFORE.replace("class Colors:", "class Colors:\n    if True:\n        x = 1\n"),  # a non-definition in a class
    BEFORE + "\ndef broken(:\n",                                      # does not parse
    None,                                                             # deleted
], ids=["import", "class-statement", "unparseable", "deleted"])
def test_what_cannot_be_narrowed_runs_the_lane(lane, after):
    assert lane.affected_names(BEFORE, after) is None


def test_a_new_file_runs_the_lane(lane):
    assert lane.affected_names(None, BEFORE) is None


def test_a_theme_colour_no_gated_test_names_does_not_reach_either_gate(lane):
    """The case that prompted the rework: the run plate's tokens, which no nanowell or remote test mentions."""
    defined = lane.definitions((ROOT / "ui" / "theme.py").read_text(encoding="utf-8"))
    names = {"run_done", "run_processing", "run_pending", "run_failed"}
    assert lane.roster_use("nanowell", "ui/theme.py", names, defined) == ""
    assert lane.roster_use("remote", "ui/theme.py", names, defined) == ""


def test_a_theme_name_a_gated_test_uses_reaches_its_gate(lane):
    """The narrowing must still fire: ``Colors`` itself (its header changed) is named by remote tests that import
    the theme, so the remote gate runs. A rule that skipped every theme change would fail here."""
    defined = lane.definitions((ROOT / "ui" / "theme.py").read_text(encoding="utf-8"))
    why = lane.roster_use("remote", "ui/theme.py", {"Colors"}, defined)
    assert why.startswith("imported directly by test_") and "Colors" in why, why


def test_reading_a_module_wholesale_counts_as_using_everything(lane):
    """``vars(theme.Colors)`` sees every member, so any member change reaches it."""
    import ast

    tree = ast.parse("from ui import theme\nnames = vars(theme.Colors)\n")
    assert lane._reads_wholesale(tree, {"Colors"})
    assert not lane._reads_wholesale(ast.parse("x = getattr(obj, 'name')\n"), {"Colors"})
    assert lane._reads_wholesale(ast.parse("x = getattr(Colors, key)\n"), {"Colors"})


def _lanes_source() -> str:
    return (ROOT / "tests" / "_lanes.py").read_text(encoding="utf-8")


def test_a_ui_roster_row_reaches_neither_gate(lane):
    src = _lanes_source()
    after = src.replace("UI_LANE_FILES: dict[str, str] = {",
                        'UI_LANE_FILES: dict[str, str] = {\n    "test_zz_new_ui.py": "a new UI test",', 1)
    assert after != src
    for gate in ("nanowell", "remote"):
        assert lane.lanes_change_reaches(gate, src, after) == "", gate


def test_a_remote_roster_row_reaches_the_remote_gate_only(lane):
    src = _lanes_source()
    after = src.replace("REMOTE_FILES: dict[str, str] = {",
                        'REMOTE_FILES: dict[str, str] = {\n    "test_zz_new_remote.py": "a new remote test",', 1)
    assert after != src
    assert "changes this lane's rosters" in lane.lanes_change_reaches("remote", src, after)
    assert lane.lanes_change_reaches("nanowell", src, after) == ""


def test_a_lanes_file_that_does_not_load_runs_the_lane(lane):
    src = _lanes_source()
    assert "cannot be compared" in lane.lanes_change_reaches("remote", src, "GATES = {")
    assert "cannot be compared" in lane.lanes_change_reaches("remote", None, src)
