"""Every page says a dataset is being opened while it is.

The opening dialog pumps the event loop on every file it reads, so the window
is repainted throughout a load. Before the pages had an "opening" state, what
was repainted was whatever they said before the load began: on a first open,
Home's "Load a directory." placeholder with its *Open a dataset* button and
recent-datasets list, for the whole length of the load — which reads as the
load having failed. ``DatasetState.opening`` is the marker, set by
``load_controller._announce_opening``; ``refresh_empty_state_pages`` lets it win
over every other reason; ``EmptyState.setBusy`` hides the controls that would
start the thing already happening.

Fast lane. The real load's sequence, through a built application, is in
``test_opening_placeholder_app.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel

from review.core import empty_state_copy as copy
from review.core.ui_helpers import (
    NO_DATASET_TITLE,
    refresh_empty_state_pages,
    wrap_with_empty_state,
)
from widgets.empty_state import EmptyState


# ── the placeholder's busy mode ─────────────────────────────────────────────

def _visible(widget, root) -> bool:
    return widget.isVisibleTo(root)


def test_busy_hides_the_button_and_the_entries_and_shows_the_hint(qtbot):
    ph = EmptyState(text="t", hint="the hint")
    qtbot.addWidget(ph)
    ph.setAction("Open a dataset", lambda: None)
    ph.setEntries([("plate", "/x", lambda: None, True, "", None)])
    assert _visible(ph._action, ph) and _visible(ph._entries, ph)
    assert not _visible(ph._hint, ph)  # the button stands in for the hint

    ph.setBusy(True)
    assert not _visible(ph._action, ph)
    assert not _visible(ph._entries, ph)
    assert _visible(ph._hint, ph)
    assert ph._hint.text() == "the hint"


def test_leaving_busy_brings_back_exactly_what_the_site_set(qtbot):
    """Including entries replaced while busy — Home repaints its recents
    during a load, and they must not be lost or shown early."""
    ph = EmptyState(text="t", hint="h")
    qtbot.addWidget(ph)
    ph.setAction("Open a dataset", lambda: None)
    ph.setBusy(True)
    ph.setEntries([("a", "", lambda: None, True, "", None),
                   ("b", "", lambda: None, True, "", None)])
    assert not _visible(ph._entries, ph)

    ph.setBusy(False)
    assert _visible(ph._action, ph)
    assert ph._action.text() == "Open a dataset"
    assert _visible(ph._entries, ph) and ph.entryCount() == 2
    assert not _visible(ph._hint, ph)


def test_a_placeholder_without_controls_is_unchanged_by_busy(qtbot):
    ph = EmptyState(text="t", hint="h")
    qtbot.addWidget(ph)
    ph.setBusy(True)
    ph.setBusy(False)
    assert not _visible(ph._action, ph) and not _visible(ph._entries, ph)
    assert _visible(ph._hint, ph)


# ── the shared refresher ────────────────────────────────────────────────────

def _window(qtbot, *, wells=None):
    dataset = SimpleNamespace(well_paths=dict(wells or {}), opening=None)
    win = SimpleNamespace(
        _app_state=SimpleNamespace(dataset=dataset,
                                   plot=SimpleNamespace(held_for="")),
        _plot_empty_stacks=[],
    )
    win._ctx = SimpleNamespace(window=win, state=win._app_state)
    body = QLabel("body")
    stack = wrap_with_empty_state(win, body, icon="home")
    qtbot.addWidget(stack)
    stack._empty_placeholder.setAction("Open a dataset", lambda: None)
    return win, stack


def test_opening_wins_over_no_dataset(qtbot, tmp_path):
    win, stack = _window(qtbot)
    win._app_state.dataset.opening = tmp_path / "plate_7"
    refresh_empty_state_pages(win)
    ph = stack._empty_placeholder
    assert stack.currentIndex() == 0
    assert ph._text.text() == "Opening plate_7…"
    assert ph._hint.text() == copy.OPENING_HINT
    assert ph.isBusy() is True


def test_opening_wins_over_the_previous_dataset_s_body(qtbot, tmp_path):
    """A switch: the stack was showing the old plate, and must not go on
    showing it under a load of the new one."""
    win, stack = _window(qtbot, wells={"A01": Path("a.csv")})
    refresh_empty_state_pages(win)
    assert stack.currentIndex() == 1
    win._app_state.dataset.opening = tmp_path / "next_plate"
    refresh_empty_state_pages(win)
    assert stack.currentIndex() == 0
    assert stack._empty_placeholder._text.text() == "Opening next_plate…"


def test_an_in_out_dataset_is_named_by_its_root_not_by_out(qtbot, tmp_path):
    (tmp_path / "screen_12" / "in").mkdir(parents=True)
    (tmp_path / "screen_12" / "out").mkdir()
    win, stack = _window(qtbot)
    win._app_state.dataset.opening = tmp_path / "screen_12" / "out"
    refresh_empty_state_pages(win)
    assert stack._empty_placeholder._text.text() == "Opening screen_12…"


def test_no_dataset_after_opening_gives_the_button_back(qtbot, tmp_path):
    win, stack = _window(qtbot)
    win._app_state.dataset.opening = tmp_path / "p"
    refresh_empty_state_pages(win)
    win._app_state.dataset.opening = None
    refresh_empty_state_pages(win)
    ph = stack._empty_placeholder
    assert ph._text.text() == NO_DATASET_TITLE
    assert ph.isBusy() is False
    assert _visible(ph._action, ph)


# ── the marker's lifetime ───────────────────────────────────────────────────

def _ctx_for(win):
    return SimpleNamespace(window=win, state=win._app_state)


def test_the_marker_is_set_for_the_block_and_cleared_after(qtbot, tmp_path):
    from review.core.load_controller import _announce_opening

    win, stack = _window(qtbot)
    ctx = _ctx_for(win)
    with _announce_opening(ctx, tmp_path / "plate"):
        assert win._app_state.dataset.opening == tmp_path / "plate"
        # Painted on entry, before the load does anything that pumps events.
        assert stack._empty_placeholder._text.text() == "Opening plate…"
    assert win._app_state.dataset.opening is None
    assert stack._empty_placeholder._text.text() == NO_DATASET_TITLE


def test_a_load_that_raises_does_not_leave_every_page_opening(qtbot, tmp_path):
    from review.core.load_controller import _announce_opening

    win, stack = _window(qtbot)
    with pytest.raises(RuntimeError):
        with _announce_opening(_ctx_for(win), tmp_path / "plate"):
            raise RuntimeError("load failed")
    assert win._app_state.dataset.opening is None
    assert stack._empty_placeholder._text.text() == NO_DATASET_TITLE


def test_the_outermost_block_owns_the_marker(qtbot, tmp_path):
    """``load_path`` wraps ``load_directory``, which announces on its own for
    Home's Reload. The inner block must neither rename nor clear it."""
    from review.core.load_controller import _announce_opening

    win, _stack = _window(qtbot)
    ctx = _ctx_for(win)
    with _announce_opening(ctx, tmp_path / "root"):
        with _announce_opening(ctx, tmp_path / "root" / "out"):
            pass
        assert win._app_state.dataset.opening == tmp_path / "root"
    assert win._app_state.dataset.opening is None
