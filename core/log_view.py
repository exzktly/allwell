"""How a log view lays out its entries: each record stamped with the time it was logged, and a blank line between
records.

Both of the app's log views go through here — the Application Log drawer (``allwell._attach_log_ring_buffer``) and
Review's own status log (``review.core.status_view``) — so they agree. A record is often more than one line (a
traceback, a run's multi-line summary, a remote command's output), and with entries packed line against line
nothing showed where one ended and the next began: a traceback's last line read as the start of the following
record. The blank line marks the boundary, and the timestamp at the head of every entry says which line starts
one.
"""

from __future__ import annotations

import logging

#: The time at the head of every entry. Time of day only: the drawer is a tail of this session, and the date sits
#: in the boot log (``startup.boot_diag``) beside it.
LOG_DATEFMT = "%H:%M:%S"
#: Between two entries: one blank line.
ENTRY_SEPARATOR = "\n\n"


def log_formatter(fmt: str) -> logging.Formatter:
    """A formatter for a log view. *fmt* must begin with ``%(asctime)s``, so every entry starts with its time."""
    if not fmt.startswith("%(asctime)s"):
        raise ValueError(f"a log view's entries start with their time: {fmt!r} does not")
    return logging.Formatter(fmt, datefmt=LOG_DATEFMT)


def entries_text(lines) -> str:
    """The plain text of a whole view refilled from kept entries, one blank line between each."""
    return ENTRY_SEPARATOR.join(lines)


def append_entry(view, text: str) -> None:
    """Append one entry to a ``QTextEdit`` (plain text or HTML), after a blank line unless it is the first.

    ``QTextEdit.append`` starts a new paragraph, so the blank line is an empty paragraph of its own; written into
    the entry's text instead, it would be lost when *text* is HTML."""
    if not view.document().isEmpty():
        view.append("")
    view.append(text)
