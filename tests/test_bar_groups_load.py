"""Loading bar groups from a user-picked file: the whole sequence, not the parse.

``review/persistence/bar_groups.py`` was one of the five modules the
combined-lane zero-coverage inventory named, and it is the one that could not
have been reached however hard the suite tried: its only route is
``runtime_app._bar_load_groups`` → ``load_via_dialog``, and ``conftest.py``'s
autouse ``_no_modal_dialogs`` makes every dialog raise. ARCHITECTURE's own
escape applies — a test that means to exercise a dialog path patches that
specific call itself — so these replace the module's ``QFileDialog`` and
``QMessageBox`` and drive the real function.

Two things shape what is asserted.

**The state is the real ``AppState``**, not a namespace built to match what the
code happens to read. A stub with exactly the attributes the code touches can
never catch the case where the shipped object has no such attribute, which is
CLAUDE.md §7.8's first face. Only ``_rebuild_all`` is a spy, and that is
deliberate: it is the *outbound call* this module has to make and the one thing
a returned value cannot show.

**Every refusal is checked for both halves** — that the message was shown *and*
that the existing groups survived. A load that reports failure and has already
half-replaced the state is worse than one that fails loudly, and the two are
indistinguishable if only the message is asserted.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from review.core.app_state import AppState  # noqa: E402
from review.persistence import bar_groups  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _App(QWidget):
    """The two things ``load_via_dialog`` uses its ``app`` for: the typed state,
    and the redraw it asks for once the load lands."""

    def __init__(self) -> None:
        super().__init__()
        self._app_state = AppState()
        self.rebuilds = 0
        self.seen_at_rebuild: list[list[tuple]] = []

    def _rebuild_all(self) -> None:
        self.rebuilds += 1
        # What the state held *at the moment the redraw was asked for*. A plain
        # counter cannot tell "applied, then redrew" from "redrew, then
        # applied", and the second one paints the previous groups and leaves
        # them on screen until something else happens to trigger a pass.
        self.seen_at_rebuild.append(
            [(s["name"], list(s["wells"])) for s in self._app_state.selection.selections]
        )


class _Dialog:
    """Stands in for ``QFileDialog``; records the arguments it was called with."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[tuple] = []

    def getOpenFileName(self, *args):
        self.calls.append(args)
        return (self.answer, "")


class _Boxes:
    """Stands in for ``QMessageBox``; records what was shown and answers the
    replace-confirmation with whatever it was told to."""

    Yes, No = 0x4000, 0x10000

    def __init__(self, answer=None) -> None:
        self.answer = self.Yes if answer is None else answer
        self.critical_calls: list[tuple] = []
        self.question_calls: list[tuple] = []

    def critical(self, *args):
        self.critical_calls.append(args)

    def question(self, *args):
        self.question_calls.append(args)
        return self.answer


V2 = {
    "schema_version": 2,
    "selections": [
        {"id": "g1", "name": "Treated", "wells": ["A1", "A2"], "color": "#112233"},
        {"id": "g2", "name": "Control", "wells": ["B1"], "color": "#445566"},
    ],
    "current_id": "g2",
}

# A real v1 block: ``members`` are rep-set *names*, resolved against
# ``rep_sets``, and a group's wells are its members' wells plus its solo wells.
# Written from what ``domain.selections_format._resolve_v1`` actually reads rather than
# from what a v1 file looks like in the imagination — an invented shape migrates
# to an empty group and the test would then pass on nothing.
V1 = {
    "rep_sets": [
        {"name": "RS1", "wells": ["D1", "D2"]},
        {"name": "Loose", "wells": ["E1"]},
    ],
    "groups": [{"name": "Legacy", "members": ["RS1"], "solo_wells": ["C5"]}],
}


@pytest.fixture
def wired(qapp, monkeypatch, tmp_path):
    """``(app, install)`` — ``install(path, answer=…)`` swaps the module's two
    dialog classes for recorders and hands them back."""
    app = _App()

    def install(answer_path: str, *, confirm=None):
        dialog, boxes = _Dialog(answer_path), _Boxes(confirm)
        monkeypatch.setattr(bar_groups, "QFileDialog", dialog)
        monkeypatch.setattr(bar_groups, "QMessageBox", boxes)
        return dialog, boxes

    return app, install


def _write(tmp_path, payload, name="groups.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _names_wells(app):
    return [(s["name"], s["wells"]) for s in app._app_state.selection.selections]


# ── the load that works ──────────────────────────────────────────────────────

def test_a_v2_file_restores_the_groups_it_names(wired, tmp_path):
    app, install = wired
    install(_write(tmp_path, V2))

    bar_groups.load_via_dialog(app)

    assert _names_wells(app) == [("Treated", ["A01", "A02"]), ("Control", ["B01"])]
    assert [s["color"] for s in app._app_state.selection.selections] == ["#112233", "#445566"]
    assert app._app_state.selection.current_selection_id == "g2", (
        "the saved current_id must survive the round trip, not reset to the first group"
    )


def test_the_load_asks_for_a_redraw(wired, tmp_path):
    """The outbound call. Restoring the state and never telling the app leaves
    every plot drawing the previous groups with nothing on screen wrong."""
    app, install = wired
    install(_write(tmp_path, V2))

    bar_groups.load_via_dialog(app)

    assert app.rebuilds == 1
    assert app.seen_at_rebuild == [[("Treated", ["A01", "A02"]), ("Control", ["B01"])]], (
        "the redraw must be asked for after the groups land, not before"
    )


def test_a_v1_file_is_migrated_rather_than_refused(wired, tmp_path):
    """Two properties in one migration, and the fixture is built so they can
    disagree. ``RS1`` is a *member* of ``Legacy``, so it is folded into that
    group's wells and does **not** also appear as a selection of its own — a
    migration that emitted every rep-set would give three entries and duplicate
    D01/D02 across two of them. ``Loose`` belongs to no group, so it must
    survive on its own; a migration that only walked ``groups`` would drop it
    and lose a rep-set silently."""
    app, install = wired
    install(_write(tmp_path, V1))

    bar_groups.load_via_dialog(app)

    assert _names_wells(app) == [("Legacy", ["D01", "D02", "C05"]), ("Loose", ["E01"])]
    assert app.rebuilds == 1


def test_the_dialog_opens_in_the_dataset_directory(wired, tmp_path):
    """Asserted on the argument handed to the dialog, because that is the only
    place the choice is visible — the function returns nothing that shows it."""
    app, install = wired
    app._app_state.dataset.data_dir = tmp_path
    dialog, _boxes = install(_write(tmp_path, V2))

    bar_groups.load_via_dialog(app)

    assert dialog.calls[0][2] == str(tmp_path)


def test_the_dialog_opens_nowhere_in_particular_with_no_dataset(wired, tmp_path):
    app, install = wired
    assert app._app_state.dataset.data_dir is None
    dialog, _boxes = install(_write(tmp_path, V2))

    bar_groups.load_via_dialog(app)

    assert dialog.calls[0][2] == ""


# ── the loads that must not happen ───────────────────────────────────────────

def _seed(app):
    """Two groups already loaded, with names nothing else in this file uses, so
    "unchanged" is a statement about these and not a coincidence."""
    bar_groups.from_dict(app, {
        "schema_version": 2,
        "selections": [{"id": "keep", "name": "Existing", "wells": ["H1"], "color": "#ABCDEF"}],
        "current_id": "keep",
    })
    return _names_wells(app)


def test_cancelling_the_dialog_changes_nothing(wired, tmp_path):
    app, install = wired
    before = _seed(app)
    _dialog, boxes = install("")          # the cancelled dialog returns ""

    bar_groups.load_via_dialog(app)

    assert _names_wells(app) == before
    assert app.rebuilds == 0
    assert boxes.critical_calls == [], "a cancel is not an error"


def test_unreadable_json_is_reported_and_the_groups_survive(wired, tmp_path):
    app, install = wired
    before = _seed(app)
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")
    _dialog, boxes = install(str(path))

    bar_groups.load_via_dialog(app)

    assert len(boxes.critical_calls) == 1
    assert _names_wells(app) == before
    assert app.rebuilds == 0


def test_a_json_file_that_is_not_an_object_is_refused(wired, tmp_path):
    """A hand-edited sidecar holding a bare list. It parses, so only the
    explicit top-level check catches it — and without that check it reaches
    ``from_block``, which returns the empty state, and the load would silently
    wipe every group."""
    app, install = wired
    before = _seed(app)
    _dialog, boxes = install(_write(tmp_path, ["not", "an", "object"], "list.json"))

    bar_groups.load_via_dialog(app)

    assert len(boxes.critical_calls) == 1
    assert _names_wells(app) == before
    assert app.rebuilds == 0


def test_a_missing_file_is_reported_rather_than_raised(wired, tmp_path):
    app, install = wired
    before = _seed(app)
    _dialog, boxes = install(str(tmp_path / "does-not-exist.json"))

    bar_groups.load_via_dialog(app)

    assert len(boxes.critical_calls) == 1
    assert _names_wells(app) == before


def test_declining_the_replace_confirmation_keeps_the_existing_groups(wired, tmp_path):
    app, install = wired
    before = _seed(app)
    _dialog, boxes = install(_write(tmp_path, V2), confirm=_Boxes.No)

    bar_groups.load_via_dialog(app)

    assert len(boxes.question_calls) == 1
    assert _names_wells(app) == before
    assert app.rebuilds == 0


def test_accepting_the_replace_confirmation_replaces_them(wired, tmp_path):
    app, install = wired
    _seed(app)
    _dialog, boxes = install(_write(tmp_path, V2), confirm=_Boxes.Yes)

    bar_groups.load_via_dialog(app)

    assert len(boxes.question_calls) == 1
    assert _names_wells(app) == [("Treated", ["A01", "A02"]), ("Control", ["B01"])]
    assert app.rebuilds == 1


def test_nothing_is_confirmed_when_there_is_nothing_to_replace(wired, tmp_path):
    """An empty plate must not be asked whether to discard its zero groups."""
    app, install = wired
    _dialog, boxes = install(_write(tmp_path, V2))

    bar_groups.load_via_dialog(app)

    assert boxes.question_calls == []
    assert app.rebuilds == 1


# ── from_dict on its own ─────────────────────────────────────────────────────

def test_from_dict_takes_the_five_values_from_block_returns(wired):
    """``from_block`` returns five values and a caller unpacking four raises. It
    is a one-line contract with no other guard, so it gets one here."""
    app, _install = wired

    bar_groups.from_dict(app, V2)

    assert _names_wells(app) == [("Treated", ["A01", "A02"]), ("Control", ["B01"])]
    assert app._app_state.selection.current_selection_id == "g2"


def test_from_dict_reads_the_dataset_labels_when_resolving_wells(wired):
    app, _install = wired
    app._app_state.dataset.tok_to_label = {"A1": "Dose 1"}

    bar_groups.from_dict(app, V2)

    assert _names_wells(app)[0] == ("Treated", ["A01", "A02"])
