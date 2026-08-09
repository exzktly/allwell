"""One notes string, two editors on screen, no way to lose an edit.

The project notes are editable in two places: Sample Definitions → Notes is the
long-form editor, and the Home page carries the same text beside the plate.
Both edit one string, ``ctx.state.selection.notes_text``.

The failure this guards is not hypothetical and is invisible to a test of
either editor alone: type on Home, switch to Sample Definitions, and if its box
still holds the previous text then focusing out of it writes that stale text
back over what was just typed. The user sees their edit appear, then vanish
some clicks later, with nothing on screen having gone wrong.

The other half is the save path. The editors commit on focus-out — deliberate,
a freeform notes box should not autosave per keystroke — so any save that does
not move focus first (the Save button, the debounced autosave, window close, a
dataset swap) has to fold the in-progress edit in before reading.

No Qt widgets here beyond stubs: the rules are in ``core/ui_helpers`` and take a
ctx, so they are exercised in the fast lane against a fake one.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.core.ui_helpers import (
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


SD, HOME = NOTES_WIDGET_KEYS


# ── the roster ──────────────────────────────────────────────────────────────

def test_both_editors_are_on_the_roster():
    assert set(NOTES_WIDGET_KEYS) == {"_notes_edit", "_home_notes_edit"}


def test_only_the_editors_that_exist_are_visited():
    ctx = _ctx(**{SD: _FakeEdit("a")})
    assert [k for k, _w in notes_widgets(ctx)] == [SD]
    assert list(notes_widgets(_ctx())) == []


# ── the clobber ─────────────────────────────────────────────────────────────

def test_committing_on_one_editor_updates_the_other():
    """The whole point. Without the mirror the second box holds stale text and
    writes it back the moment it loses focus."""
    sd, home = _FakeEdit("old note"), _FakeEdit("new note")
    ctx = _ctx(**{SD: sd, HOME: home})

    notes_schedule_save(ctx, widget_key=HOME)

    assert ctx.state.selection.notes_text == "new note"
    assert sd.toPlainText() == "new note"
    assert ctx.persistence.scheduled == ["notes"]


def test_the_stale_editor_cannot_write_back_over_the_edit():
    """The sequence as a user performs it: type on Home, visit Sample
    Definitions, focus out there. The second commit must be a no-op, not a
    revert."""
    sd, home = _FakeEdit("old note"), _FakeEdit("old note")
    ctx = _ctx(**{SD: sd, HOME: home})

    home._text = "the incubator alarmed overnight"
    notes_schedule_save(ctx, widget_key=HOME)      # Home loses focus
    notes_schedule_save(ctx, widget_key=SD)        # …then so does the other box

    assert ctx.state.selection.notes_text == "the incubator alarmed overnight"
    assert home.toPlainText() == "the incubator alarmed overnight"


def test_mirroring_never_reads_as_an_edit():
    sd, home = _FakeEdit("old"), _FakeEdit("new")
    ctx = _ctx(**{SD: sd, HOME: home})
    ctx.state.selection.notes_text = "new"

    mirror_notes(ctx, source_key=HOME)

    assert sd.set_while_blocked == [True], "signals must be blocked during the mirror"
    assert home.set_while_blocked == [], "the source editor is left alone"


def test_an_editor_already_showing_the_text_is_not_touched():
    sd = _FakeEdit("same")
    ctx = _ctx(**{SD: sd, HOME: _FakeEdit("same")})
    ctx.state.selection.notes_text = "same"

    mirror_notes(ctx, source_key=HOME)
    assert sd.set_while_blocked == []


def test_one_editor_alone_still_works():
    sd = _FakeEdit("only box")
    ctx = _ctx(**{SD: sd})
    notes_schedule_save(ctx, widget_key=SD)
    assert ctx.state.selection.notes_text == "only box"
    assert ctx.persistence.scheduled == ["notes"]


def test_a_save_scheduled_with_no_editors_at_all_is_harmless():
    ctx = _ctx()
    notes_schedule_save(ctx, widget_key=SD)
    assert ctx.persistence.scheduled == ["notes"]


# ── the save path ───────────────────────────────────────────────────────────

def test_a_save_that_never_moved_focus_still_captures_the_edit():
    """The Save button, the debounced autosave, window close, a dataset swap.

    None of them move focus, and the editors commit on focus-out — so without
    this the save persists whatever the slice last held and silently drops
    what is on screen.
    """
    home = _FakeEdit("typed but not committed", focused=True)
    ctx = _ctx(**{SD: _FakeEdit("stale"), HOME: home})

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "typed but not committed"


def test_an_unfocused_pair_leaves_the_slice_alone():
    """Neither box is being typed in, so the slice is already the truth and
    must not be overwritten by whichever editor happens to be listed first."""
    ctx = _ctx(**{SD: _FakeEdit("box a"), HOME: _FakeEdit("box b")})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "committed earlier"


def test_only_the_focused_editor_wins():
    sd = _FakeEdit("sample definitions text", focused=True)
    home = _FakeEdit("home text")
    ctx = _ctx(**{SD: sd, HOME: home})

    commit_focused_notes(ctx)
    assert ctx.state.selection.notes_text == "sample definitions text"


# ── clearing ────────────────────────────────────────────────────────────────

def test_a_dataset_swap_blanks_every_editor():
    sd, home = _FakeEdit("previous dataset"), _FakeEdit("previous dataset")
    ctx = _ctx(**{SD: sd, HOME: home})

    clear_notes_editors(ctx)

    assert sd.toPlainText() == "" and home.toPlainText() == ""
    assert sd.set_while_blocked == [True] and home.set_while_blocked == [True]


# ── teardown races ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn", [
    lambda ctx: commit_focused_notes(ctx),
    lambda ctx: mirror_notes(ctx, source_key="_notes_edit"),
    lambda ctx: clear_notes_editors(ctx),
])
def test_a_deleted_widget_does_not_take_the_save_down(fn):
    """A page torn down mid-save leaves a live Python wrapper over a dead C++
    object. Losing the notes to that is worse than skipping one editor."""
    ctx = _ctx(**{SD: _FakeEdit("alive"), HOME: _DeadEdit()})
    ctx.state.selection.notes_text = "alive"
    fn(ctx)


def test_an_unfocused_edit_is_still_captured_when_only_one_box_differs():
    """Focus alone is not enough to find the in-progress edit.

    On a window that is closing — or one that never had keyboard focus, which
    is every window under an offscreen Qt platform — each editor reports no
    focus while one still holds uncommitted text. That is precisely the save
    this has to protect, so the single editor differing from the slice wins.
    """
    home = _FakeEdit("typed, never focused")
    ctx = _ctx(**{SD: _FakeEdit("committed earlier"), HOME: home})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "typed, never focused"


def test_two_divergent_boxes_are_left_alone_rather_than_guessed_between():
    """Cannot happen while the mirror works. If it ever did, picking one would
    silently discard the other — leaving the slice is the recoverable answer."""
    ctx = _ctx(**{SD: _FakeEdit("box a"), HOME: _FakeEdit("box b")})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "committed earlier"


def test_focus_still_wins_over_the_divergence_fallback():
    focused = _FakeEdit("the one being typed in", focused=True)
    ctx = _ctx(**{SD: _FakeEdit("also different"), HOME: focused})
    ctx.state.selection.notes_text = "committed earlier"

    commit_focused_notes(ctx)

    assert ctx.state.selection.notes_text == "the one being typed in"
