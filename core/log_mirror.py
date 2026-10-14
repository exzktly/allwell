"""Copy the warnings and errors each pane's own log shows into the app log.

The application log (the Help drawer, ``⌘L``, filled by
``allwell._attach_log_ring_buffer``) sees every ``logging`` record, but two of
the app's busiest surfaces never produce one. The Analyze pane's run log is the
pipeline subprocess's stdout, read line by line and written straight into a
widget; each Datasets tool page's raw log is fed by its worker's ``logLine``
signal. A failure reported there — a per-well traceback, a refused merge, a
remote fetch that came back short — reached only that pane, so someone reading
the app log to find out what went wrong saw a clean session.

:func:`mirror_line` is the one call both panes make for every line they show.
It forwards only the lines that are warnings or errors: the app log is where
someone looks for what went wrong, and a pipeline run says tens of thousands of
ordinary things that already have a home in the Analyze pane.

The records go to ``allwell.<source>`` loggers, which propagate to the root
logger the ring buffer is attached to. Deliberately *not* under ``"review"``:
that logger also feeds the Review pane's bottom status log, which is Review's
own diary, and an Analyze run's warnings do not belong in it.

Nothing in the app log is fed back into either pane, so mirroring cannot loop.
"""

from __future__ import annotations

import logging
import re

from core.safe import swallowed

#: The ``asctime  LEVEL`` prefix ``process_microscopy`` writes on every line.
#: The app log stamps its own time and level, so a forwarded line keeps only
#: its message rather than carrying two timestamps.
_RE_OWN_PREFIX = re.compile(
    r"^\S+\s+\S+\s+(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+")

#: How the Datasets tools report a job that failed: ``✗ <message>``, written by
#: every tool controller's failure handler.
_FAILURE_MARK = "✗"

_LEVELS = {"WARNING": logging.WARNING, "ERROR": logging.ERROR}


def severity(text: str, tag: str | None = None) -> str:
    """The severity tag (``INFO`` / ``WARNING`` / ``ERROR``) of one log line.

    *tag* is the pane's own verdict when it already has one — the Analyze pane
    classifies every line before colouring it, and the two must agree. Without
    one, a Datasets line that opens with the failure mark is an error, and
    anything else goes through the same classifier the Analyze pane uses.
    """
    if tag is not None:
        return tag
    if text.lstrip().startswith(_FAILURE_MARK):
        return "ERROR"
    # Late import: ``services`` is Qt-free and has no reason to load before
    # the first line arrives.
    from services.pipeline_runner import classify_log_line
    return classify_log_line(text)


def mirror_line(source: str, text: str, tag: str | None = None) -> None:
    """Log *text* to ``allwell.<source>`` if it is a warning or an error.

    Safe from any thread (``logging`` is, and the app log's handler marshals to
    the GUI thread itself) and never raises — a pane's own log must keep
    working whatever the app log does with the line.
    """
    try:
        level = _LEVELS.get(severity(text, tag))
        if level is None:
            return
        message = _RE_OWN_PREFIX.sub("", text.strip(), count=1)
        if message:
            logging.getLogger(f"allwell.{source}").log(level, message)
    except Exception:
        swallowed()
