"""Back and forward through the places a user has been — Qt-free.

A *place* is any hashable value the window chooses (the window records
``(mode, section, page)``; see ``ui/page_history.py``). The rules are a
browser's:

* visiting a place adds it after the current one and drops everything ahead of
  the current one — going somewhere new after going back forgets the old way
  forward;
* visiting the place you are already at adds nothing, so a signal that fires
  twice for one move (a mode re-announced after a relayout) is one entry;
* :meth:`back` and :meth:`forward` move the cursor and return the place to go
  to, or ``None`` at an end; the caller goes there inside :meth:`replaying`, so
  the visits that going there causes are not recorded as new ones.

Listeners hear every change of what :meth:`can_back` / :meth:`can_forward`
answer, so a button can be enabled exactly when it can do something.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager

#: How many places are kept. A session of hours of browsing pages is a few
#: hundred moves; the oldest go first.
MAX_ENTRIES = 200


class NavHistory:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: list[Hashable] = []
        self._index = -1
        self._replaying = 0
        self._max = max(2, int(max_entries))
        self._listeners: list[Callable[[], None]] = []

    # ── reading ─────────────────────────────────────────────────────────────
    def current(self) -> Hashable | None:
        """The place the cursor is at, or ``None`` before the first visit."""
        return self._entries[self._index] if self._index >= 0 else None

    def entries(self) -> list[Hashable]:
        return list(self._entries)

    def can_back(self) -> bool:
        return self._index > 0

    def can_forward(self) -> bool:
        return 0 <= self._index < len(self._entries) - 1

    def is_replaying(self) -> bool:
        return self._replaying > 0

    # ── changing ────────────────────────────────────────────────────────────
    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def visit(self, place: Hashable) -> bool:
        """Record arriving at *place*. Returns whether an entry was added."""
        if place is None or self._replaying or place == self.current():
            return False
        del self._entries[self._index + 1:]
        self._entries.append(place)
        if len(self._entries) > self._max:
            del self._entries[: len(self._entries) - self._max]
        self._index = len(self._entries) - 1
        self._notify()
        return True

    def back(self) -> Hashable | None:
        """Step back; the place to go to, or ``None`` at the start."""
        if not self.can_back():
            return None
        self._index -= 1
        self._notify()
        return self._entries[self._index]

    def forward(self) -> Hashable | None:
        """Step forward; the place to go to, or ``None`` at the end."""
        if not self.can_forward():
            return None
        self._index += 1
        self._notify()
        return self._entries[self._index]

    @contextmanager
    def replaying(self) -> Iterator[None]:
        """Going to a place back / forward returned: nothing is recorded."""
        self._replaying += 1
        try:
            yield
        finally:
            self._replaying -= 1

    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn()
