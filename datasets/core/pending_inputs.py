"""Inputs a queued job has still to write.

When the Job Queue opens a tool's page to set up the next step after a queued
job — "Analyze the output of that Concatenate" — the input field holds the
folder that job is set up to write, and that folder does not exist yet. The
page must neither call it an error nor read it: the field says whose output it
is, everything that would read the input is deferred to the run, and the page's
own Run button is off, because running now would read a folder that is not
there. Queueing is what the page is for in that state.

Qt only through :class:`widgets.path_field.PathField`; no page knowledge.
"""

from __future__ import annotations

from collections.abc import Mapping

from widgets.path_field import PathField

#: The Run button's tooltip while an input is pending.
RUN_WAITS = ("An input is the output of a queued job that hasn't run yet — "
             "add this to the queue to run it after that job.")


def apply_pending(fields: Mapping[str, PathField], pending: Mapping[str, str]) -> None:
    """Mark each field named in *pending* as waiting, with its note, and
    clear the mark from every other field in *fields*.

    *fields* maps a spec key (``"dataset1"``) to the field showing it, and
    *pending* the same keys to the sentence the field shows.
    """
    for key, field in fields.items():
        note = pending.get(key, "")
        if note:
            field.set_pending(field.path(), note)
        elif field.is_pending():
            field.set_path(field.path())


def any_pending(fields: Mapping[str, PathField]) -> bool:
    return any(field.is_pending() for field in fields.values())
