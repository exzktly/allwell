"""The per-CSV repaint during a dataset load must not dispatch user input.

``load_directory`` empties ``dataset.cache`` and ``dataset.well_paths`` in
``begin_load`` and then refills them one CSV at a time, pumping the event loop
after each file so the window keeps repainting — on a network share the load
takes long enough that a frozen window reads as a hang. ``processEvents()``
with no flags dispatches *everything* queued, so a click landing between two
files runs the sidebar, commit and tab handlers against a dataset that is
neither the old one nor the new one.

The pump is kept; the input dispatch is not. Long operations that offer a
Cancel button still need theirs, so ``allow_user_input`` defaults to True and
the load path is the caller that opts out.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop  # noqa: E402

from review.core import load_controller, ui_helpers  # noqa: E402
from tests._repo_scan import parse_source


class _RecordingApplication:
    def __init__(self):
        self.calls: list = []

    def processEvents(self, *args):
        self.calls.append(args)


def test_the_default_pump_still_dispatches_user_input(monkeypatch):
    rec = _RecordingApplication()
    monkeypatch.setattr(ui_helpers, "QApplication", rec)
    ui_helpers._pump(allow_user_input=True)
    assert rec.calls == [()]


def test_opting_out_excludes_user_input(monkeypatch):
    rec = _RecordingApplication()
    monkeypatch.setattr(ui_helpers, "QApplication", rec)
    ui_helpers._pump(allow_user_input=False)
    assert rec.calls == [(QEventLoop.ExcludeUserInputEvents,)]


def test_the_load_loop_opts_out_on_every_progress_call():
    """The guard: a future edit that drops the keyword re-opens the window."""
    src = textwrap.dedent(inspect.getsource(load_controller._load_directory_held))
    fn = ast.parse(src).body[0]

    progress_calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"_show_progress", "_step_progress"}
    ]
    assert progress_calls, (
        "load_directory no longer calls the progress helpers by name — if it "
        "went back through the app shim, this guard cannot see the keyword"
    )
    for call in progress_calls:
        opt_out = any(
            kw.arg == "allow_user_input"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is False
            for kw in call.keywords
        )
        assert opt_out, (
            f"{call.func.id} at line {call.lineno} of load_directory pumps the "
            "event loop with user input enabled, mid-load"
        )


def test_no_bare_processevents_anywhere_in_the_load_controller():
    """The scan surface is half the rule (CLAUDE.md 7.5 #3).

    The first version of this guard read only calls named ``_show_progress`` /
    ``_step_progress`` inside ``load_directory``. ``_opening_set_phase`` — a
    sibling pump called from the *same loop iteration*, one line later — was
    invisible to it, so on the main open path (the only one that builds the
    opening dialog) user input was dispatched once per CSV anyway and the
    opt-out held only on the dialog-less reload route.
    """
    import pathlib

    path = pathlib.Path(load_controller.__file__)
    tree = parse_source(path)
    bare = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "processEvents" and not node.args):
            bare.append(node.lineno)
    assert not bare, (
        f"bare processEvents() at line(s) {bare} of {path.name}: every pump on "
        "the load path has to pass ExcludeUserInputEvents, or a click landing "
        "between two CSVs runs against a half-built dataset"
    )
