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


def test_show_note_overrides_the_validator_hint_until_the_next_revalidate(qapp):
    """Regression guard (UX audit P1-3): a destination field's validator
    hint is styled "Danger" (red) regardless of what it says -- reusing it
    verbatim right after a successful run to explain "this folder now has
    content" reads as an error about the run that just succeeded, not the
    neutral, expected fact it actually is. show_note() must display instead,
    styled "Muted" not "Danger", and a later revalidate() must discard it
    and fall back to the real validator message again."""
    field = PathField(
        "Label", pick=lambda current, parent=None: "",
        validator=lambda p: "Already has content — choose an empty folder.",
    )
    field.set_path("/tmp/dest")
    assert field.message() == "Already has content — choose an empty folder."
    assert field._hint.objectName() == "Danger"

    field.show_note("This destination now holds this run's output.")
    assert field._hint.text() == "This destination now holds this run's output."
    assert field._hint.objectName() == "Muted"
    # The validator's own verdict (is_valid()/message()) is untouched by the
    # note -- only the displayed hint text/style changes. The run gate must
    # still correctly refuse to run again into this same folder.
    assert not field.is_valid()
    assert field.message() == "Already has content — choose an empty folder."

    field.revalidate()
    assert field._hint.objectName() == "Danger"
    assert field._hint.text() == "Already has content — choose an empty folder."


def test_set_locked_disables_only_browse_not_the_whole_field(qapp):
    """Regression guard (UX audit P2-4): every page used to lock a field for
    a run's duration with setEnabled(False), which disables the *whole*
    widget — including self._edit, which is already read-only and has
    nothing interactive about it to disable. The visible effect was the
    path text (exactly what a user watching a long run most wants to check)
    rendered at the lowest-contrast, disabled-looking style on the page.
    setLocked() must disable only the Browse button."""
    field = PathField("Label", pick=lambda current, parent=None: "/picked")
    field.set_path("/tmp/dest")
    assert field.isEnabled() is True
    assert field._browse_btn.isEnabled() is True

    field.setLocked(True)
    assert field.isEnabled() is True  # the field itself stays fully legible
    assert field._browse_btn.isEnabled() is False

    field.setLocked(False)
    assert field._browse_btn.isEnabled() is True


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
