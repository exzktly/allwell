"""One notes string, one editor on screen, no way to lose an edit.

The project notes are edited in one place — Home's box beside the plate — over
one string, ``ctx.state.selection.notes_text``. There used to be a second editor
on a page of its own, and the helpers in ``core/ui_helpers`` still fan out over
a roster rather than naming a widget: that is what made a second editor safe to
add and then safe to remove, and it is what the hydrator uses to push text that
came off disk into whatever boxes exist.

What can still go wrong, and what each block below pins:

* The editor commits on focus-out — deliberate, a freeform notes box should not
  autosave per keystroke. So any save that does *not* move focus first (the Save
  button, the debounced autosave, window close, a dataset swap) has to fold the
  in-progress edit in before reading, or it persists whatever the slice last
  held and silently drops what is on screen.
* Under an offscreen Qt platform nothing ever holds keyboard focus, so "the
  focused editor" is not a question that can always be answered. The fallback
  has to find the edit anyway.
* Writing text *into* the box must never itself read as an edit, or a hydrate
  would schedule a save of what it just loaded.

No Qt widgets here beyond stubs: the rules take a ctx, so they run in the fast
lane against a fake one.
"""

from __future__ import annotations

import types

import pytest

from review.core.ui_helpers import (
    NOTES_WIDGET_KEYS,
    clear_notes_editors,
    commit_focused_notes,
    mirror_notes,
    notes_schedule_save,
    notes_widgets,
)


class _FakeEdit:
    """A notes box: text, focus, and whether signals were blocked while set."""

    def __init__(self, text: str = "", *, focused: bool = False):
        self._text = text
        self._focused = focused
        self.signals_blocked = False
        self.set_while_blocked: list[bool] = []

    def toPlainText(self) -> str:
        return self._text

    def setPlainText(self, text: str) -> None:
        self.set_while_blocked.append(self.signals_blocked)
        self._text = text

    def clear(self) -> None:
        self.set_while_blocked.append(self.signals_blocked)
        self._text = ""

    def hasFocus(self) -> bool:
        return self._focused

    def blockSignals(self, on: bool) -> bool:
        previous = self.signals_blocked
        self.signals_blocked = on
        return previous


class _DeadEdit(_FakeEdit):
    """A widget whose C++ side has gone — a page torn down mid-save."""

    def toPlainText(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    def hasFocus(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    def setPlainText(self, text):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    def clear(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")


class _Widgets:
    def __init__(self, mapping):
        self._m = dict(mapping)

    def get_or_none(self, key):
        return self._m.get(key)


class _Persistence:
    def __init__(self):
        self.scheduled: list[str] = []

    def schedule_save(self, domain):
        self.scheduled.append(domain)


def _ctx(**editors):
    return types.SimpleNamespace(
        widgets=_Widgets(editors),
        persistence=_Persistence(),
        state=types.SimpleNamespace(
            selection=types.SimpleNamespace(notes_text="")),
    )


HOME = "_home_notes_edit"


# ── the roster ──────────────────────────────────────────────────────────────

def test_home_carries_the_only_notes_editor():
    assert NOTES_WIDGET_KEYS == (HOME,)


def test_only_the_editors_that_exist_are_visited():
    """The page builds lazily and a profile can drop it, so the roster naming a
    key is not a promise that the widget is there."""
    ctx = _ctx(**{HOME: _FakeEdit("a")})
    assert [k for k, _w in notes_widgets(ctx)] == [HOME]
    assert list(notes_widgets(_ctx())) == []


# ── committing an edit ──────────────────────────────────────────────────────

def test_a_commit_captures_the_text_and_schedules_the_save():
    home = _FakeEdit("the incubator alarmed overnight")
    ctx = _ctx(**{HOME: home})

    notes_schedule_save(ctx, widget_key=HOME)

    assert ctx.state.selection.notes_text == "the incubator alarmed overnight"
    assert ctx.persistence.scheduled == ["notes"]


def test_a_save_scheduled_with_no_editor_at_all_is_harmless():
    """A dataset can be saved before the page has ever been built."""
    ctx = _ctx()
    notes_schedule_save(ctx, widget_key=HOME)
    assert ctx.persistence.scheduled == ["notes"]


# ── the save path ───────────────────────────────────────────────────────────

def test_a_save_that_never_moved_focus_still_captures_the_edit():
    """The Save button, the debounced autosave, window close, a dataset swap.

    None of them move focus, and the editor commits on focus-out — so without
    this the save persists whatever the slice last held and silently drops what
    is on screen.
    """
    home = _FakeEdit("typed but not committed", focused=True)
    ctx = _ctx(**{HOME: home})

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "typed but not committed"


def test_an_unfocused_edit_is_still_captured():
    """Focus alone is not enough to find the in-progress edit.

    On a window that is closing — or one that never had keyboard focus, which is
    every window under an offscreen Qt platform — the editor reports no focus
    while still holding uncommitted text. That is precisely the save this has to
    protect, so an editor differing from the slice wins without needing focus.
    """
    ctx = _ctx(**{HOME: _FakeEdit("typed, never focused")})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "typed, never focused"


def test_an_editor_agreeing_with_the_slice_leaves_it_alone():
    """Nothing is being typed, so the slice is already the truth."""
    ctx = _ctx(**{HOME: _FakeEdit("committed earlier")})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "committed earlier"


# ── loading text in ─────────────────────────────────────────────────────────

def test_text_loaded_from_disk_reaches_the_box():
    """``source_key=""`` means the text came off disk rather than from a box, so
    every editor is a destination. This is the hydrator's path
    (``persistence.sample_definitions._apply_unified_state``)."""
    home = _FakeEdit("previous dataset")
    ctx = _ctx(**{HOME: home})
    ctx.state.selection.notes_text = "loaded from the sidecar"

    mirror_notes(ctx, source_key="")

    assert home.toPlainText() == "loaded from the sidecar"


def test_writing_text_in_never_reads_as_an_edit():
    """Signals stay blocked, or the hydrate schedules a save of what it loaded."""
    home = _FakeEdit("previous dataset")
    ctx = _ctx(**{HOME: home})
    ctx.state.selection.notes_text = "loaded from the sidecar"

    mirror_notes(ctx, source_key="")

    assert home.set_while_blocked == [True]


def test_the_editor_that_committed_is_left_alone():
    home = _FakeEdit("new")
    ctx = _ctx(**{HOME: home})
    ctx.state.selection.notes_text = "new"

    mirror_notes(ctx, source_key=HOME)

    assert home.set_while_blocked == []


def test_an_editor_already_showing_the_text_is_not_touched():
    home = _FakeEdit("same")
    ctx = _ctx(**{HOME: home})
    ctx.state.selection.notes_text = "same"

    mirror_notes(ctx, source_key="")
    assert home.set_while_blocked == []


# ── clearing ────────────────────────────────────────────────────────────────

def test_a_dataset_swap_blanks_the_editor():
    home = _FakeEdit("previous dataset")
    ctx = _ctx(**{HOME: home})

    clear_notes_editors(ctx)

    assert home.toPlainText() == ""
    assert home.set_while_blocked == [True]


# ── teardown races ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn", [
    lambda ctx: commit_focused_notes(ctx),
    lambda ctx: mirror_notes(ctx, source_key=""),
    lambda ctx: clear_notes_editors(ctx),
])
def test_a_deleted_widget_does_not_take_the_save_down(fn):
    """A page torn down mid-save leaves a live Python wrapper over a dead C++
    object. Losing the notes to that is worse than skipping the editor."""
    ctx = _ctx(**{HOME: _DeadEdit()})
    ctx.state.selection.notes_text = "alive"
    fn(ctx)


# ── the roster's fan-out, with more than one editor on it ───────────────────
#
# There is one editor today, so the branches below are unreachable through the
# real roster. They are still the reason ``core/ui_helpers`` fans out over a
# roster instead of naming a widget — the module says a second editor is safe to
# add, and this is what makes that claim checkable rather than a hope. A second
# roster entry that arrives without these holding would bring back the original
# bug: a stale box writing its text over a fresh edit on focus-out.

@pytest.fixture()
def two_editors(monkeypatch):
    """Put a second key on the roster, the way adding an editor would."""
    import review.core.ui_helpers as uh
    keys = ("_home_notes_edit", "_second_notes_edit")
    monkeypatch.setattr(uh, "NOTES_WIDGET_KEYS", keys)
    return keys


def test_committing_on_one_editor_updates_the_other(two_editors):
    home_key, other_key = two_editors
    home, other = _FakeEdit("new note"), _FakeEdit("old note")
    ctx = _ctx(**{home_key: home, other_key: other})

    notes_schedule_save(ctx, widget_key=home_key)

    assert ctx.state.selection.notes_text == "new note"
    assert other.toPlainText() == "new note"


def test_the_stale_editor_cannot_write_back_over_the_edit(two_editors):
    """The sequence as a user performs it: type in one box, visit the other,
    focus out there. The second commit must be a no-op, not a revert."""
    home_key, other_key = two_editors
    home, other = _FakeEdit("old note"), _FakeEdit("old note")
    ctx = _ctx(**{home_key: home, other_key: other})

    home._text = "the incubator alarmed overnight"
    notes_schedule_save(ctx, widget_key=home_key)   # the edited box loses focus
    notes_schedule_save(ctx, widget_key=other_key)  # …then so does the other

    assert ctx.state.selection.notes_text == "the incubator alarmed overnight"
    assert other.toPlainText() == "the incubator alarmed overnight"


def test_only_the_focused_editor_wins(two_editors):
    home_key, other_key = two_editors
    ctx = _ctx(**{home_key: _FakeEdit("home text"),
                  other_key: _FakeEdit("the one being typed in", focused=True)})

    commit_focused_notes(ctx)
    assert ctx.state.selection.notes_text == "the one being typed in"


def test_two_divergent_boxes_are_left_alone_rather_than_guessed_between(two_editors):
    """Cannot happen while the mirror works. If it ever did, picking one would
    silently discard the other — leaving the slice is the recoverable answer."""
    home_key, other_key = two_editors
    ctx = _ctx(**{home_key: _FakeEdit("box a"), other_key: _FakeEdit("box b")})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "committed earlier"


def test_focus_still_wins_over_the_divergence_fallback(two_editors):
    home_key, other_key = two_editors
    ctx = _ctx(**{home_key: _FakeEdit("also different"),
                  other_key: _FakeEdit("the one being typed in", focused=True)})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)
    assert ctx.state.selection.notes_text == "the one being typed in"


def test_a_dataset_swap_blanks_every_editor(two_editors):
    home_key, other_key = two_editors
    home, other = _FakeEdit("previous dataset"), _FakeEdit("previous dataset")
    ctx = _ctx(**{home_key: home, other_key: other})

    clear_notes_editors(ctx)

    assert home.toPlainText() == "" and other.toPlainText() == ""
    assert home.set_while_blocked == [True] and other.set_while_blocked == [True]
