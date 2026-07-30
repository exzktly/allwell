"""Bare-`QApplication` widget tests for `widgets.path_field.PathField`.

Same pattern as `tests/test_concat_dashboard.py` — never the `viewer_app`/
`loaded_app` full-app fixtures (CLAUDE.md §2's per-test RSS leak).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.path_field import PathField, folder_picker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_path_updates_state(qapp):
    field = PathField("Label", pick=lambda current, parent=None: "")
    assert field.path() == ""
    assert not field.is_valid()

    field.set_path("/some/folder")
    assert field.path() == "/some/folder"
    assert field.is_valid()


def test_validator_drives_hint_and_validity(qapp):
    field = PathField(
        "Label", pick=lambda current, parent=None: "",
        validator=lambda p: "bad path" if p.name == "bad" else "",
    )
    field.set_path("/tmp/good")
    assert field.is_valid()
    assert field.message() == ""

    field.set_path("/tmp/bad")
    assert not field.is_valid()
    assert field.message() == "bad path"
    assert not field._hint.isHidden()


def test_no_validator_is_always_valid_once_set(qapp):
    field = PathField("Label", pick=lambda current, parent=None: "")
    field.set_path("/tmp/anything")
    assert field.is_valid()
    assert field.message() == ""


def test_browse_never_reaches_a_real_dialog(qapp):
    # Regression guard: PathField._browse must call the injected `pick`
    # callable rather than a hardcoded QFileDialog — the autouse
    # `_no_modal_dialogs` fixture (tests/conftest.py) would otherwise raise
    # on a real dialog call under offscreen QPA.
    calls = []

    def _pick(current, parent=None):
        calls.append((current, parent))
        return "/picked"

    field = PathField("Label", pick=_pick)
    field._browse()
    assert calls == [("", field)]
    assert field.path() == "/picked"


def test_changed_signal_emits_on_browse(qapp):
    field = PathField("Label", pick=lambda current, parent=None: "/x")
    seen = []
    field.changed.connect(lambda: seen.append(True))
    field._browse()
    assert seen == [True]


def test_folder_picker_forwards_parent(monkeypatch, qapp):
    from PySide6.QtWidgets import QFileDialog

    captured = {}

    def _fake_get_existing_directory(parent, prompt, current, options):
        captured["parent"] = parent
        captured["prompt"] = prompt
        return "/chosen"

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", _fake_get_existing_directory)
    picker = folder_picker("Pick a folder")
    field = PathField("Label", pick=picker)
    field._browse()
    assert captured["parent"] is field
    assert captured["prompt"] == "Pick a folder"
    assert field.path() == "/chosen"
